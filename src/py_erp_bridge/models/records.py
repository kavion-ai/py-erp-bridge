"""Pydantic models for canonical ERP records.

The same model is used for reads and writes. Only fields that were explicitly set are
sent on writes. ``raw`` always holds the untouched provider payload on reads, and
``provider_fields`` lets callers add provider-native fields on writes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from py_erp_bridge.types import JsonObject


class CanonicalModel(BaseModel):
    """Common settings: unknown fields are rejected so typos never vanish silently."""

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True, validate_assignment=True)


class LineItem(CanonicalModel):
    """One line of an order or invoice."""

    line_number: str | None = None
    product_id: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    raw: JsonObject = Field(default_factory=dict, repr=False)
    provider_fields: JsonObject = Field(default_factory=dict)


class ERPRecord(CanonicalModel):
    """Fields shared by all records."""

    id: str | None = None
    provider: str | None = None
    modified_at: datetime | None = None
    raw: JsonObject = Field(default_factory=dict, repr=False)
    provider_fields: JsonObject = Field(default_factory=dict)


class CustomRecord(ERPRecord):
    """Record of an entity without a canonical model; use ``raw`` / ``provider_fields``."""


class Party(ERPRecord):
    """Fields common to customers and vendors."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = None
    currency: str | None = None
    street: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None


class Customer(Party):
    """A customer / debtor."""


class Vendor(Party):
    """A vendor / supplier / creditor."""


class Product(ERPRecord):
    """A product, material or item."""

    sku: str | None = None
    name: str | None = None
    description: str | None = None
    unit: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    category: str | None = None


class InventoryItem(ERPRecord):
    """Stock of one product at one location."""

    product_id: str | None = None
    location_id: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None


class Document(ERPRecord):
    """Fields common to commercial documents."""

    number: str | None = None
    currency: str | None = None
    status: str | None = None
    total: Decimal | None = None
    items: list[LineItem] = Field(default_factory=list)


class SalesOrder(Document):
    """A sales order."""

    customer_id: str | None = None
    order_date: date | None = None


class PurchaseOrder(Document):
    """A purchase order."""

    vendor_id: str | None = None
    order_date: date | None = None


class Invoice(Document):
    """A customer (accounts receivable) invoice."""

    customer_id: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    balance: Decimal | None = None


class Payment(ERPRecord):
    """A payment or receipt."""

    number: str | None = None
    party_id: str | None = None
    payment_date: date | None = None
    amount: Decimal | None = None
    currency: str | None = None
    method: str | None = None
    reference: str | None = None


class Employee(ERPRecord):
    """An employee / worker."""

    number: str | None = None
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    department: str | None = None
    title: str | None = None


class FinancialRecord(ERPRecord):
    """A journal entry, journal line or comparable ledger record."""

    number: str | None = None
    account_id: str | None = None
    posting_date: date | None = None
    amount: Decimal | None = None
    debit: Decimal | None = None
    credit: Decimal | None = None
    currency: str | None = None
    description: str | None = None
