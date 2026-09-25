# A synchronous call to another system between the check and the write; flagged only when configured.
def charge(repo, billing, invoice_id):
    invoice = repo.get(invoice_id)
    if invoice.paid:  # expect: check
        return
    billing.call_billing(invoice_id)  # expect: gap second-read
    repo.mark_paid(invoice_id)  # expect: write
