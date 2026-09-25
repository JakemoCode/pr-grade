# A model class looked up by name is not a read of shared state, so a check inside the transaction that
# uses it opens no window before the transaction.
from django.db import transaction


def fix_totals(apps, _schema_editor):
    Order = apps.get_model('order', 'Order')
    while True:
        with transaction.atomic():
            pks = list(Order.objects.filter(total__lt=0).select_for_update().values_list('pk', flat=True))
            if not pks:
                break
            Order.objects.filter(pk__in=pks).update(total=0)
