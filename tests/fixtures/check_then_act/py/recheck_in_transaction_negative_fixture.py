# The check is repeated under a row lock inside the write's transaction, and the fetch_ helpers after it
# read the database, not another system.
from django.db import transaction


def complete(checkout_pk):
    checkout = Checkout.objects.filter(pk=checkout_pk).first()
    if not checkout:
        return None
    with transaction.atomic():
        checkout = Checkout.objects.select_for_update().filter(pk=checkout_pk).first()
        if not checkout:
            return None
        lines = fetch_checkout_lines(checkout)
        checkout.save(update_fields=['lines'])
        return lines
