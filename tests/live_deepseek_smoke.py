"""Explicit minimal real API check; excluded from offline test discovery."""

import llm
from config import redact


def main():
    try:
        result = llm.complete_json('仅返回 JSON：{"connected":true}。', {"purpose": "验证模型访问与 JSON 返回，不生成营销文案。"}, max_tokens=128)
        if result.get("connected") is not True:
            print("模型已响应，但 JSON 内容不符合检查要求。")
            return 1
        print("DeepSeek 真实连接成功，JSON 返回检查通过。")
        return 0
    except llm.ModelError as error:
        print(redact(str(error)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
