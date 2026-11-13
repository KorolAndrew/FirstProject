# Check how to get short answers without long "thinking" from qwen3.5-9b via LM Studio.
import json, time, urllib.request

def ask(extra, sys_add="", user="Привет! Кто ты? Ответь одним предложением."):
    body = {"model": "qwen/qwen3.5-9b", "max_tokens": 300, "temperature": 0.7,
            "messages": [{"role": "system", "content": "Ты Янка, помощница Яны." + sys_add},
                         {"role": "user", "content": user}], **extra}
    req = urllib.request.Request("http://127.0.0.1:1234/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
    m = r["choices"][0]["message"]
    print(json.dumps({"extra": extra, "sys": sys_add, "sec": round(time.time() - t, 1),
                      "content": (m.get("content") or "")[:200], "reasoning_len": len(m.get("reasoning_content") or m.get("reasoning") or ""),
                      "keys": list(m.keys()), "usage": r.get("usage")}, ensure_ascii=False))

ask({})
ask({"reasoning_effort": "none"})
ask({"chat_template_kwargs": {"enable_thinking": False}})
ask({}, sys_add=" /no_think")
