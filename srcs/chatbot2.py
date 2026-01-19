import requests

def stream_ollama(prompt: str, model: str = "llama3"):
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True
    }

    with requests.post(url, json=payload, stream=True) as response:
        for line in response.iter_lines():
            if line:
                data = line.decode("utf-8")
                if data.startswith("data:"):
                    content = data[5:].strip()
                    if content:
                        part = eval(content)  # API 回傳的是 JSON 字串
                        print(part.get("response", ""), end="", flush=True)

# 測試串流
stream_ollama("Explain how to design a small aquaponic system.")