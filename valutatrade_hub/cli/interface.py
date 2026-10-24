"""Argument parsing, interactive sessions and rendering without business logic."""

import argparse
import shlex
import sys

from prettytable import PrettyTable

from ..core.currencies import CryptoCurrency, get_currency, get_supported_codes
from ..core.exceptions import ApiRequestError, CurrencyNotFoundError, StorageError
from ..core.usecases import CoreService
from ..logging_config import configure_logging, redact_secrets
from ..parser_service.config import ParserConfig

COMMAND_DESCRIPTIONS = {
    "register": "зарегистрировать пользователя и пустой портфель",
    "login": "войти в текущую сессию",
    "show-portfolio": "показать кошельки и общую стоимость",
    "buy": "увеличить количество выбранной валюты",
    "sell": "уменьшить количество валюты при достаточном остатке",
    "get-rate": "получить свежий курс и обратное значение",
    "update-rates": "обновить реальные курсы из внешних источников",
    "show-rates": "показать курсы из локального кеша",
}


class CommandParser(argparse.ArgumentParser):
    """Report parse errors without terminating a running interactive session."""

    def error(self, message: str) -> None:
        """Let the CLI boundary format argument failures safely."""
        raise ValueError(f"Некорректные аргументы: {message}")


def build_parser() -> tuple[CommandParser, dict]:
    """Describe exactly the assignment commands plus a safe config diagnostic."""
    parser = CommandParser(
        prog="valutatrade",
        description="Учебный виртуальный портфель фиатных и криптовалют.",
        epilog="Без команды — интерактивная сессия. В ней доступны help и exit.",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="проверить наличие ключа без его вывода",
    )
    subparsers = parser.add_subparsers(dest="command")
    commands = {
        name: subparsers.add_parser(name, help=description, description=description)
        for name, description in COMMAND_DESCRIPTIONS.items()
    }
    for name in ("register", "login"):
        commands[name].add_argument("--username", required=True)
        commands[name].add_argument("--password", required=True)
    commands["show-portfolio"].add_argument("--base")
    for name in ("buy", "sell"):
        commands[name].add_argument("--currency", required=True)
        commands[name].add_argument("--amount", required=True)
    commands["get-rate"].add_argument("--from", dest="from_code", required=True)
    commands["get-rate"].add_argument("--to", dest="to_code", required=True)
    commands["update-rates"].add_argument(
        "--source", choices=("coingecko", "exchangerate")
    )
    commands["show-rates"].add_argument("--currency")
    commands["show-rates"].add_argument("--top", type=int)
    commands["show-rates"].add_argument("--base")
    return parser, commands


def print_config_status() -> None:
    """Report only key presence, never secret values or authenticated URLs."""
    config = ParserConfig()
    fiat_status = "ключ задан" if config.EXCHANGERATE_API_KEY else "ключ не задан"
    print(f"ExchangeRate-API: {fiat_status}")
    print("CoinGecko: публичный режим без ключа")
    print("Проверено только наличие настроек; запросы к API не выполнялись.")


def format_amount(amount: float, code: str) -> str:
    """Use readable fiat/crypto precision without rounding tiny holdings to zero."""
    digits = 4 if isinstance(get_currency(code), CryptoCurrency) else 2
    if amount and abs(amount) < 10**-digits:
        return f"{amount:.8g}"
    return f"{amount:.{digits}f}"


def format_rate(rate: float) -> str:
    """Keep useful quote precision for both small inverse and large direct rates."""
    formatted = f"{rate:.8f}".rstrip("0").rstrip(".")
    return f"{rate:.8g}" if rate and formatted == "0" else formatted


def render_portfolio(result: dict) -> None:
    """Render domain-calculated balances and totals through PrettyTable."""
    username, base = result["username"], result["base"]
    if not result["wallets"]:
        print(f"Портфель пользователя '{username}' пуст.")
        return
    print(f"Портфель пользователя '{username}' (база: {base}):")
    table = PrettyTable(["Валюта", "Баланс", f"Стоимость ({base})"])
    for wallet in result["wallets"]:
        code = wallet["currency_code"]
        table.add_row(
            [code, format_amount(wallet["balance"], code), f"{wallet['value']:,.2f}"]
        )
    print(table)
    print(f"ИТОГО: {result['total']:,.2f} {base}")


def render_trade(result: dict, *, selling: bool) -> None:
    """Show the agreed unit-balance changes and an informational USD valuation."""
    code = result["currency_code"]
    action = "Продажа" if selling else "Покупка"
    valuation = "Оценочная выручка" if selling else "Оценочная стоимость покупки"
    quoted_rate = (
        f"{result['rate']:.2f}" if result["rate"] >= 1 else format_rate(result["rate"])
    )
    print(
        f"{action} выполнена: {format_amount(result['amount'], code)} {code} "
        f"по курсу {quoted_rate} USD/{code}"
    )
    print("Изменения в портфеле:")
    print(
        f"- {code}: было {format_amount(result['before'], code)} "
        f"→ стало {format_amount(result['after'], code)}"
    )
    print(f"{valuation}: {result['value']:,.2f} USD")


def render_quote(quote) -> None:
    """Separate cache receipt time from the provider's underlying quote time."""
    print(
        f"Курс {quote.from_code}→{quote.to_code}: {format_rate(quote.rate)} "
        f"(обновлено: {quote.updated_at})"
    )
    print(
        f"Обратный курс {quote.to_code}→{quote.from_code}: "
        f"{format_rate(quote.inverse_rate)}"
    )
    for source, timestamp in sorted(quote.provider_times.items()):
        print(f"Время котировки у {source}: {timestamp}")


def render_rates(result: dict) -> None:
    """Display cached quotes with explicit per-pair stale warnings."""
    print(f"Rates from cache (updated at {result['last_refresh']}):")
    for quote in result["quotes"]:
        status = "УСТАРЕЛ" if quote.stale else "кеш актуален"
        print(
            f"- {quote.from_code}_{quote.to_code}: {format_rate(quote.rate)} "
            f"(обновлено: {quote.updated_at}; {status})"
        )
    if any(quote.stale for quote in result["quotes"]):
        print("В кеше есть устаревшие курсы. Выполните 'update-rates'.")


def dispatch(arguments: argparse.Namespace, service: CoreService) -> int:
    """Invoke use cases and choose their renderer; do not perform domain validation."""
    command = arguments.command
    if command == "register":
        user = service.register(arguments.username, arguments.password)
        print(
            f"Пользователь '{user.username}' зарегистрирован (id={user.user_id}). "
            f"Войдите: login --username {shlex.quote(user.username)} --password ****"
        )
    elif command == "login":
        user = service.login(arguments.username, arguments.password)
        print(f"Вы вошли как '{user.username}'")
    elif command == "show-portfolio":
        render_portfolio(service.show_portfolio(arguments.base))
    elif command in ("buy", "sell"):
        try:
            amount = float(arguments.amount)
        except (ValueError, OverflowError):
            raise ValueError("'amount' должен быть положительным числом") from None
        operation = service.sell if command == "sell" else service.buy
        render_trade(operation(arguments.currency, amount), selling=command == "sell")
    elif command == "get-rate":
        render_quote(service.get_rate(arguments.from_code, arguments.to_code))
    elif command == "update-rates":
        result = service.update_rates(arguments.source)
        if result.errors:
            print("Update completed with errors. Check logs/parser.log for details.")
            for source, message in sorted(result.errors.items()):
                print(redact_secrets(f"- {source}: {message}"))
            print(f"Total rates updated: {result.updated_count}.")
            return 1
        else:
            print(
                f"Update successful. Total rates updated: {result.updated_count}. "
                f"Last refresh: {result.last_refresh}"
            )
    elif command == "show-rates":
        render_rates(
            service.show_rates(arguments.currency, arguments.top, arguments.base)
        )
    return 0


def print_error(error: Exception) -> None:
    """Translate expected failures into safe, actionable console output."""
    print(redact_secrets(str(error)))
    if isinstance(error, CurrencyNotFoundError):
        print("Поддерживаемые валюты: " + ", ".join(get_supported_codes()))
        print("Справка: help get-rate")
    elif isinstance(error, ApiRequestError):
        print(
            "Проверьте сеть и повторите позже; для обновления выполните update-rates."
        )


def execute_tokens(tokens: list[str], service, parser, commands) -> int:
    """Execute a command without swallowing unexpected programming bugs."""
    if tokens[0] == "help":
        if len(tokens) == 1:
            parser.print_help()
        elif len(tokens) == 2 and tokens[1] in commands:
            commands[tokens[1]].print_help()
        else:
            print("Неизвестная команда справки")
            return 2
        return 0
    try:
        arguments = parser.parse_args(tokens)
        if arguments.check_config:
            print_config_status()
        elif arguments.command:
            return dispatch(arguments, service)
        else:
            parser.print_help()
        return 0
    except SystemExit as error:
        return int(error.code or 0)  # --help should not close an interactive session.
    except (ValueError, ApiRequestError, StorageError, OSError) as error:
        print_error(error)
        return 1


def run_interactive(service, parser, commands) -> int:
    """Keep login state until exit, EOF or Ctrl+C; recover after user-input errors."""
    print("ValutaTrade Hub — учебный виртуальный портфель.")
    print(
        "Команды: " + ", ".join(COMMAND_DESCRIPTIONS) + ". Справка: help. Выход: exit."
    )
    while True:
        try:
            line = input("> ")
            tokens = shlex.split(line)
            if not tokens:
                continue
            if tokens == ["exit"]:
                return 0
            execute_tokens(tokens, service, parser, commands)
        except ValueError as error:
            print_error(error)
        except (EOFError, KeyboardInterrupt):
            print()
            return 0


def main(argv: list[str] | None = None) -> int:
    """Run a standalone command or an authenticated interactive CLI session."""
    tokens = list(sys.argv[1:] if argv is None else argv)
    parser, commands = build_parser()
    if tokens and tokens[0] == "help":
        return execute_tokens(tokens, None, parser, commands)
    try:
        arguments = parser.parse_args(tokens)
        if arguments.check_config:
            print_config_status()
            return 0
        service = CoreService()
        configure_logging(service.settings)
    except (ValueError, StorageError, OSError) as error:
        print_error(error)
        return 1
    if arguments.command:
        return execute_tokens(tokens, service, parser, commands)
    return run_interactive(service, parser, commands)


if __name__ == "__main__":
    raise SystemExit(main())
