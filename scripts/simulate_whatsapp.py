import argparse
import json
import os

import requests


def main() -> None:
    parser = argparse.ArgumentParser(description="Simula envio de mensagem para o webhook WhatsApp")
    parser.add_argument("--url", default="http://127.0.0.1:8000/webhook/whatsapp")
    parser.add_argument("--remetente", required=True)
    parser.add_argument("--mensagem", required=True)
    parser.add_argument("--token", default=os.getenv("WEBHOOK_TOKEN"))
    args = parser.parse_args()

    if not args.token:
        raise ValueError("Informe --token ou configure WEBHOOK_TOKEN no ambiente.")

    payload = {
        "remetente": args.remetente,
        "mensagem": args.mensagem,
    }

    response = requests.post(
        args.url,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Token": args.token,
        },
        timeout=20,
    )

    print(f"Status: {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(response.text)


if __name__ == "__main__":
    main()
