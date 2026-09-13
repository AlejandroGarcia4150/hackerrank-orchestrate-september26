from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from datetime import date
import calendar

ZERO = Decimal('0')
CENT = Decimal('.01')

def money(value):
    if value is None or str(value).strip() == '':
        raise ValueError('Missing monetary amount; never implicitly zero')
    result = Decimal(str(value).replace(',', '').strip())
    if not result.is_finite() or result < ZERO:
        raise ValueError('Amount must be finite and nonnegative')
    return result

def rounded(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)

def floor_money(value):
    return value.quantize(CENT, rounding=ROUND_FLOOR)

def fmt(value):
    return format(value, '.2f').rstrip('0').rstrip('.')

def day(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Date must use YYYY-MM-DD')
    return parsed

def month_add(value, months, anchor=None):
    n = value.year * 12 + value.month - 1 + months
    year, month = divmod(n, 12)
    month += 1
    return date(year, month, min(anchor or value.day, calendar.monthrange(year, month)[1]))

def items(value):
    return set(filter(None, value.split('|')))

class Rates:
    def __init__(self, rows):
        self.rates = {}
        for r in rows:
            key = (day(r['rate_date']), r['from_currency'], r['to_currency'])
            amount = money(r['rate'])
            if amount <= ZERO or (key in self.rates and self.rates[key] != amount):
                raise ValueError('Conflicting or invalid exchange rate')
            self.rates[key] = amount

    def convert(self, amount, currency, home, when):
        if currency == home:
            return rounded(amount)
        key = (when, currency, home)
        if key not in self.rates:
            raise ValueError(f'Missing exact dated FX {when}:{currency}/{home}')
        return rounded(amount * self.rates[key])
