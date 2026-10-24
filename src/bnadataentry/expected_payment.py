"""Expected-payment strategies, one per supermarket.

Rates that the business treats as fixed live on the strategy class so a
future change is a one-line edit. Rates that vary per payment (Capital
delivery charge, One Stop Mart back margin and delivery incentive) are
passed in as decimal fractions: ``0.04`` means 4%.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

CAPITAL = "Capital"
MAKRO = "Makro"
ONE_STOP_MART = "One Stop Mart"
SUPERMARKETS = (CAPITAL, MAKRO, ONE_STOP_MART)

TAX_INCLUDED = "tax_included"
TAX_EXCLUSIVE = "tax_exclusive"
NO_TAX_NO_RETRO = "no_tax_no_retro"


def money(value: float) -> float:
    """Round to cents, nudging binary-float noise away from the boundary."""
    return round(float(value) + 1e-9, 2)


@dataclass(frozen=True)
class PaymentTerms:
    """Inputs the expected-payment formula is allowed to see."""

    total_invoice_amount: float
    is_tax_included: bool = False
    retro_percentage: float = 0.0
    delivery_charge_percentage: float = 0.0
    tax_percentage: float = 0.0
    tax_treatment: str = ""
    discount_percentage: float = 0.0
    exempt_amount: float = 0.0


@dataclass(frozen=True)
class ExpectedPayment:
    expected_final_payment: float
    steps: tuple[tuple[str, str], ...] = field(default_factory=tuple)


class PaymentStrategy(ABC):
    """Calculate the amount the supermarket should transfer."""

    name: str

    @abstractmethod
    def calculate(self, terms: PaymentTerms) -> ExpectedPayment:
        """Return the expected bank transfer and the steps used."""


def capital_tax_included(_terms: PaymentTerms) -> bool:
    """Capital item prices already include 5% tax."""
    return True


def _percent_label(rate: float) -> str:
    return f"{rate * 100:.2f}%"


class CapitalStrategy(PaymentStrategy):
    """User-chosen retro, then delivery off the invoice balance."""

    name = CAPITAL

    def calculate(self, terms: PaymentTerms) -> ExpectedPayment:
        gross = float(terms.total_invoice_amount)
        discount_rate = float(terms.discount_percentage)
        discount = gross * discount_rate
        net = gross - discount
        retro_rate = float(terms.retro_percentage)
        retro = net * retro_rate
        invoice_balance = net - retro
        dc_rate = float(terms.delivery_charge_percentage)
        exempt_fraction = 0.0
        if gross > 0:
            exempt_fraction = min(max(float(terms.exempt_amount), 0.0), gross) / gross
        exempt_balance = invoice_balance * exempt_fraction
        taxable_balance = invoice_balance - exempt_balance
        steps: list[tuple[str, str]] = [
            ("Invoice amount", f"{money(gross):,.2f}"),
        ]
        if discount_rate:
            steps.append(
                (
                    f"Discount {_percent_label(discount_rate)}",
                    f"-{money(discount):,.2f}",
                )
            )
            steps.append(("Amount after discount", f"{money(net):,.2f}"))
        steps.extend(
            [
                (
                    f"Retro {_percent_label(retro_rate)}",
                    f"-{money(retro):,.2f}",
                ),
                ("Invoice balance", f"{money(invoice_balance):,.2f}"),
            ]
        )
        taxable_base = (taxable_balance / 105.0) * 100.0
        taxable_delivery = taxable_base * dc_rate
        exempt_delivery = exempt_balance * dc_rate
        if exempt_fraction:
            steps.append(
                (
                    "Taxable ex-tax base (balance / 105 × 100)",
                    f"{money(taxable_base):,.2f}",
                )
            )
            steps.append(
                (
                    f"Taxable delivery {dc_rate:.0%}",
                    f"-{money(taxable_delivery):,.2f}",
                )
            )
            steps.append(
                (
                    f"Tax-exempt delivery {dc_rate:.0%}",
                    f"-{money(exempt_delivery):,.2f}",
                )
            )
        else:
            steps.append(
                (
                    "Ex-tax base (balance / 105 × 100)",
                    f"{money(taxable_base):,.2f}",
                )
            )
            steps.append(
                (
                    f"Delivery charge {dc_rate:.0%}",
                    f"-{money(taxable_delivery):,.2f}",
                )
            )
        expected = invoice_balance - taxable_delivery - exempt_delivery
        steps.append(("Expected final payment", f"{money(expected):,.2f}"))
        return ExpectedPayment(money(expected), tuple(steps))


class MakroStrategy(PaymentStrategy):
    """Add the tax percent the user entered, then deduct their retro percent."""

    name = MAKRO

    def calculate(self, terms: PaymentTerms) -> ExpectedPayment:
        gross = float(terms.total_invoice_amount)
        discount_rate = float(terms.discount_percentage)
        discount = gross * discount_rate
        net = gross - discount
        exempt_fraction = 0.0
        if gross > 0:
            exempt_fraction = min(max(float(terms.exempt_amount), 0.0), gross) / gross
        exempt_net = net * exempt_fraction
        taxable_net = net - exempt_net
        tax_rate = float(terms.tax_percentage)
        tax = taxable_net * tax_rate
        grand_total = taxable_net + tax + exempt_net
        retro_rate = float(terms.retro_percentage)
        retro = grand_total * retro_rate
        expected = grand_total - retro
        steps: list[tuple[str, str]] = [
            ("Invoice amount", f"{money(gross):,.2f}"),
        ]
        if discount_rate:
            steps.append(
                (
                    f"Discount {_percent_label(discount_rate)}",
                    f"-{money(discount):,.2f}",
                )
            )
            steps.append(("Amount after discount", f"{money(net):,.2f}"))
        if exempt_fraction:
            steps.append(("Taxable amount", f"{money(taxable_net):,.2f}"))
            steps.append((f"Tax {_percent_label(tax_rate)}", f"+{money(tax):,.2f}"))
            steps.append(("Tax-exempt amount", f"{money(exempt_net):,.2f}"))
        else:
            steps.append((f"Tax {_percent_label(tax_rate)}", f"+{money(tax):,.2f}"))
        steps.extend(
            [
                ("Grand total", f"{money(grand_total):,.2f}"),
                (f"Retro {_percent_label(retro_rate)}", f"-{money(retro):,.2f}"),
                ("Expected final payment", f"{money(expected):,.2f}"),
            ]
        )
        return ExpectedPayment(money(expected), tuple(steps))


class OneStopMartStrategy(PaymentStrategy):
    """Back margin plus delivery incentive, both taken off the invoice total."""

    name = ONE_STOP_MART

    def calculate(self, terms: PaymentTerms) -> ExpectedPayment:
        gross = float(terms.total_invoice_amount)
        discount_rate = float(terms.discount_percentage)
        net = gross * (1.0 - discount_rate)
        retro_rate = float(terms.retro_percentage)
        delivery_rate = float(terms.delivery_charge_percentage)
        combined_rate = retro_rate + delivery_rate
        combined = net * combined_rate
        expected = net - combined
        steps: list[tuple[str, str]] = [
            ("Invoice amount", f"{money(gross):,.2f}"),
        ]
        if discount_rate:
            steps.append(
                (
                    f"Discount {_percent_label(discount_rate)}",
                    f"-{money(gross - net):,.2f}",
                )
            )
            steps.append(("Amount after discount", f"{money(net):,.2f}"))
        steps.extend(
            [
                (f"Back margin {retro_rate:.2%}", f"{retro_rate:.2%}"),
                (f"Delivery incentive {delivery_rate:.2%}", f"{delivery_rate:.2%}"),
                ("Combined discount", f"-{money(combined):,.2f}"),
                ("Expected final payment", f"{money(expected):,.2f}"),
            ]
        )
        return ExpectedPayment(money(expected), tuple(steps))


_STRATEGIES: dict[str, PaymentStrategy] = {
    CAPITAL: CapitalStrategy(),
    MAKRO: MakroStrategy(),
    ONE_STOP_MART: OneStopMartStrategy(),
}


def strategy_for(supermarket_name: str) -> PaymentStrategy:
    try:
        return _STRATEGIES[supermarket_name]
    except KeyError as exc:
        raise ValueError(f"Unknown supermarket: {supermarket_name}") from exc


def expected_payment(supermarket_name: str, terms: PaymentTerms) -> ExpectedPayment:
    return strategy_for(supermarket_name).calculate(terms)
