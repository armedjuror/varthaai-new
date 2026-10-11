from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _dec(value):
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError):
        return Decimal('0')


@register.filter
def inr(value):
    """Indian digit grouping with 2 decimals: 1,23,456.70"""
    d = _dec(value).quantize(Decimal('0.01'))
    sign = '-' if d < 0 else ''
    whole, frac = f'{abs(d):.2f}'.split('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ','.join(groups + [tail])
    return f'{sign}{whole}.{frac}'


@register.filter
def rate(value):
    d = _dec(value)
    return f'{d.normalize():f}' if d == d.to_integral() else f'{d:.2f}'.rstrip('0')


@register.filter
def half_rate(value):
    return rate(_dec(value) / 2)


@register.filter
def qty(value):
    d = _dec(value).quantize(Decimal('0.001'))
    text = f'{d:f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


@register.simple_tag
def pin_state_script():
    """Prefix tables + helper for prefilling a state <select> from a pincode."""
    from django.templatetags.static import static
    from django.utils.html import format_html, json_script

    from billing.gst_states import pincode_prefix_map

    return format_html('{}<script src="{}"></script>', json_script(pincode_prefix_map(), 'pinStateMap'),
                       static('js/state-from-pincode.js'))


@register.simple_tag
def gst_state_options(selected=''):
    from django.utils.html import format_html, format_html_join

    from billing.gst_states import STATES

    return format_html_join('', '<option value="{}"{}>{}</option>', (
        (code, format_html(' selected') if code == selected else '', name) for code, name in STATES
    ))
