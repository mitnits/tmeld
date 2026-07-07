"""Hand-written stand-in for upstream meld/conf.py (a build-time
template). Vendored modules only import the i18n helpers from it;
tmeld ships untranslated, so these are identity functions."""


def _(message: str) -> str:
    return message


def ngettext(singular: str, plural: str, n: int) -> str:
    return singular if n == 1 else plural
