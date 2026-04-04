from database import DatabaseClient


def main() -> None:
    db = DatabaseClient()
    result = db.backfill_caixas_from_history(clear_movements=False)

    print("Backfill de caixas concluido.")
    print("Antes:")
    for moeda in ["XAU", "EUR", "USD", "SRD", "BRL"]:
        print(f"  {moeda}: {result.get('before', {}).get(moeda, '0')}")

    print("Depois:")
    for moeda in ["XAU", "EUR", "USD", "SRD", "BRL"]:
        print(f"  {moeda}: {result.get('after', {}).get(moeda, '0')}")


if __name__ == "__main__":
    main()
