"""OpenAI 兼容 provider：任何提供 /chat/completions 的服务都能接。

配置（环境变量，或 `mentor/config.json` 里的同名键）：

| 变量 | 说明 |
|---|---|
| `MENTOR_LLM_BASE` | 例如 `https://api.deepseek.com/v1` |
| `MENTOR_LLM_KEY` | API key（只从环境/配置文件读，不落库、不回显） |
| `MENTOR_LLM_MODEL` | 例如 `deepseek-chat` |
| `MENTOR_LLM_TIMEOUT` | 秒，默认 60 |
| `MENTOR_LLM_STREAM` | `0` 关闭流式 |

设计取舍：用 `urllib` 而不是 requests，是为了守住"纯标准库"这条线；
流式用 SSE + 增量 JSON 扫描，组件一到就交给界面，不必等整段生成完。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterator

from ..protocol import WIDGET_TYPES
from .base import GenerateRequest, Provider, ProviderError

SYSTEM_PROMPT = """你是 mentor，一个"资料来源接地"的学习助手。你面对的是一个学习笔记本，里面有若干材料片段，每段都带编号 [n]。

硬性规则（违反即视为失败）：
1. 只依据给定片段回答。片段里没有的内容，直接说"材料里没有"，不要用你的先验知识补。
2. 每一句事实性断言后面必须带 [n] 引用；一个编号都没用到的断句会被系统删掉。
3. 只能输出下面组件目录里的类型，且只输出"本次计划"里列出的类型。
4. 输出**严格 JSON**，形如 {"widgets":[{"type":"answer","title":"…","data":{…},"cites":[1,3]}]}，不要包 markdown 代码块，不要解释。
5. 语言跟随提问语言（中文问就中文答）。术语保留英文原词。
6. answer 组件用 markdown，短句、要点化，不要长篇大论；数字、公式、代码必须与原文一致。

组件目录（data 的字段形状）：
%s
""" % "\n".join(f"- `{name}`: {shape}" for name, shape in WIDGET_TYPES.items())


class OpenAICompatProvider(Provider):
    name = "openai-compatible"

    def __init__(self, base_url: str = "", api_key: str = "", model: str = "",
                 timeout: float = 60.0, stream: bool = True, temperature: float = 0.2) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.stream = stream
        self.temperature = temperature

    @classmethod
    def from_env(cls, cfg: dict[str, Any] | None = None) -> "OpenAICompatProvider":
        cfg = cfg or {}
        pick = lambda key, env, default="": cfg.get(key) or os.environ.get(env) or default
        stream_raw = pick("stream", "MENTOR_LLM_STREAM", "1")
        return cls(
            base_url=pick("base_url", "MENTOR_LLM_BASE"),
            api_key=pick("api_key", "MENTOR_LLM_KEY"),
            model=pick("model", "MENTOR_LLM_MODEL"),
            timeout=float(pick("timeout", "MENTOR_LLM_TIMEOUT", "60")),
            stream=str(stream_raw) not in ("0", "false", "False"),
        )

    def available(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    # ------------------------------------------------------------------ 生成
    def generate(self, request: GenerateRequest) -> list[dict[str, Any]]:
        return list(self.generate_stream(request))

    def generate_stream(self, request: GenerateRequest) -> Iterator[dict[str, Any]]:
        if not self.available():
            raise ProviderError("未配置 MENTOR_LLM_BASE / MENTOR_LLM_KEY / MENTOR_LLM_MODEL")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._user_prompt(request)},
            ],
            "temperature": self.temperature,
            "stream": bool(self.stream),
        }
        if self.stream:
            yield from self._stream_widgets(payload)
        else:
            body = self._post(payload)
            for widget in _widgets_from_text(_content_of(body)):
                yield widget

    def _user_prompt(self, request: GenerateRequest) -> str:
        planned = "、".join(f"`{t}`" for t in request.planned) or "`answer`"
        history = ""
        if request.history:
            lines = "\n".join(f"- {q}" for q in request.history[-3:])
            history = f"这是接着上一轮问的，前面问过：\n{lines}\n\n"
        return (
            f"本次计划：{planned}\n"
            f"意图：{request.intent}；版面：{request.layout}\n"
            f"{history}"
            f"问题：{request.question}\n\n"
            f"可用材料片段：\n{request.brief()}\n\n"
            "只输出 JSON。"
        )

    # ------------------------------------------------------------------ 传输
    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
                "Accept": "text/event-stream, application/json"}

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._endpoint(), data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:  # pragma: no cover - 依赖网络
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise ProviderError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:  # pragma: no cover - 依赖网络
            raise ProviderError(f"请求失败：{exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"响应不是 JSON：{raw[:200]}") from exc

    def _stream_widgets(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._endpoint(), data=data, headers=self._headers(), method="POST")
        scanner = _WidgetScanner()
        saw_content = False
        saw_frame = False
        collected: list[bytes] = []
        collected_bytes = 0
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw_line in resp:
                    # 留一份原始响应：有的兼容端点会忽略 stream:true，直接回完整 JSON
                    if collected_bytes < 2_000_000:
                        collected.append(raw_line)
                        collected_bytes += len(raw_line)
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk in ("", "[DONE]"):
                        continue
                    try:
                        frame = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    saw_frame = True
                    delta = _delta_of(frame)
                    if not delta:
                        continue
                    saw_content = True
                    for widget in scanner.feed(delta):
                        yield widget
        except urllib.error.HTTPError as exc:  # pragma: no cover - 依赖网络
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise ProviderError(f"HTTP {exc.code}: {detail}") from exc
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - 依赖网络
            raise ProviderError(f"流式请求失败：{exc}") from exc

        if not saw_frame:
            # 没有一帧 SSE：可能是端点忽略了 stream:true（很多兼容实现如此），
            # 那就把整个响应体当一次完整响应解析。不认这种情况会**静默丢掉模型的回答**。
            text = b"".join(collected).decode("utf-8", errors="replace")
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = None
            if isinstance(body, dict):
                content = _content_of(body)
                if content:
                    saw_content = True
                    for widget in scanner.feed(content):
                        yield widget

        for widget in scanner.flush():
            yield widget

        # 上游"成功返回"但内容根本不可用时，必须显式报错。
        # 否则流会安静地结束、tutor 以为模型答了，界面就把离线兜底的结果
        # 当成模型生成的内容展示（degraded 还是 False）——这是欺骗用户。
        if not saw_content:
            detail = "响应既不是 SSE，也不是可解析的完整 JSON" if not saw_frame else "内容帧里没有正文"
            raise ProviderError(f"上游返回为空（{detail}）")


def _content_of(body: dict[str, Any]) -> str:
    try:
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"响应结构不认识：{str(body)[:200]}") from exc


def _delta_of(frame: dict[str, Any]) -> str:
    try:
        return frame["choices"][0].get("delta", {}).get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _widgets_from_text(text: str) -> list[dict[str, Any]]:
    payload = _loads_loose(text)
    widgets = payload.get("widgets") if isinstance(payload, dict) else None
    if not isinstance(widgets, list):
        raise ProviderError("模型没有返回 {\"widgets\": [...]}")
    return [w for w in widgets if isinstance(w, dict)]


def _loads_loose(text: str) -> Any:
    """容忍模型包了 markdown 代码块或前后有解释文字。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError(f"JSON 解析失败：{exc}") from exc
    raise ProviderError("没有找到 JSON 对象")


class _WidgetScanner:
    """增量扫描 `{"widgets":[ {...}, {...} ]}`，凑齐一个对象就交出去。"""

    def __init__(self) -> None:
        self.buffer = ""
        self.pos = 0
        self.started = False
        self._decoder = json.JSONDecoder()

    def feed(self, delta: str) -> Iterator[dict[str, Any]]:
        self.buffer += delta
        if not self.started:
            marker = self.buffer.find("[")
            key = self.buffer.find('"widgets"')
            if key < 0 or marker < 0 or marker < key:
                return
            self.pos = marker + 1
            self.started = True
        while True:
            snippet = self.buffer[self.pos :].lstrip()
            skipped = len(self.buffer[self.pos :]) - len(snippet)
            self.pos += skipped
            if not snippet or snippet[0] in "]}":
                return
            if snippet[0] == ",":
                self.pos += 1
                continue
            if snippet[0] != "{":
                self.pos += 1
                continue
            try:
                obj, end = self._decoder.raw_decode(self.buffer, self.pos)
            except ValueError:
                return  # 还没收完，等下一段
            self.pos = end
            if isinstance(obj, dict):
                yield obj

    def flush(self) -> Iterator[dict[str, Any]]:
        yield from self.feed("")
