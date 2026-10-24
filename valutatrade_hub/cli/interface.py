"""Expose the prepared environment without pretending trading is implemented."""

import argparse

from ..parser_service.config import ParserConfig


def print_config_status() -> None:
    """Report only key presence, never secret values or authenticated URLs."""
    config = ParserConfig()
    fiat_status = "ключ задан" if config.EXCHANGERATE_API_KEY else "ключ не задан"
    crypto_status = (
        "Demo-ключ задан"
        if config.COINGECKO_DEMO_API_KEY
        else "публичный режим без ключа"
    )
    print(f"ExchangeRate-API: {fiat_status}")
    print(f"CoinGecko: {crypto_status}")
    print("Проверено только наличие настроек; запросы к API не выполнялись.")


def main(argv: list[str] | None = None) -> None:
    """Start the scaffold or inspect its local service configuration."""
    parser = argparse.ArgumentParser(
        prog="valutatrade",
        description="ValutaTrade Hub — каркас итогового проекта, не готовое решение.",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="проверить наличие API-ключей без вывода значений и сетевых запросов",
    )
    arguments = parser.parse_args(argv)
    if arguments.check_config:
        print_config_status()
        return
    print("ValutaTrade Hub: окружение подготовлено, бизнес-логика ещё не реализована.")
    parser.print_help()


if __name__ == "__main__":
    main()
