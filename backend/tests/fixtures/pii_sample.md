<!--
SYNTHETIC TEST DATA — every person, address, and identifier in this file is
fictional, created for PII-detection tests. Reserved test ranges only:
555-01xx phones, 000-prefix SSN shape, 192.0.2.0/24 documentation IPs,
Visa test card number. This fixture exercises PII inside markdown
structures: headings, tables, inline code, fenced code, and link targets.
-->

# Relocation request — Maria Clara Reyes

Maria Clara Reyes (born 14 March 1991) has requested relocation to the
Manila office effective January 2027. Her manager, Juan dela Cruz, approved
the request on 21 August 2026.

## Contact details

| Person | Email | Phone | Class expected |
| --- | --- | --- | --- |
| Maria Clara Reyes | mc.reyes.demo@gmail.com | +63 917 555 0123 | personal |
| Juan dela Cruz | juan.delacruz@acmecorp.example | (202) 555-0181 | ambiguous |
| HR service desk | hr@acmecorp.example | (202) 555-0180 | business |

Her current residential address is **12 Sampaguita Street, Barangay San
Isidro, Quezon City 1100**. The government ID on file is `000-12-3456`,
and payroll holds the card `4111 1111 1111 1111` until the transfer
completes.

Reach her via the [intranet directory](mailto:mc.reyes.demo@gmail.com) or
see the [office map](https://acmecorp.example/contact) for the published
headquarters address: 500 Harbor Boulevard, Suite 900, Pasig City.

## Access log excerpt

```text
2026-08-21T08:15:32 login user=mreyes src=192.0.2.44 status=ok
2026-08-21T08:16:04 mail-to=mc.reyes.demo@gmail.com subject="stipend"
```

## Not PII — traps, none of these should fire

- Purchase order `ORD-000-12-3456` shipped from the Pasig warehouse.
- Build version `10.4.0.1` deployed at `08:15:32` with zero errors.
- Part number `555-0100-A` replaces the `555-0099-A` bracket.
- Table 1 reports revenue of 1534.9 against 1411.3 the prior year.
