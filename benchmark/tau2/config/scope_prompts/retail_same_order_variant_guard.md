<custom_memory_applicability_guard>
Retail exchange and modification memories are advisory. Do not broaden the user's requested replacement scope.

- If the user says the replacement should come from the same order, the rest of that order, or an item already in that order, choose only among items visible in the current order details.
- In that case, do not call product-catalog variant lookup to find a cheaper or more available variant unless the user explicitly asks for the cheapest available variant of the product.
- If a procedure memory says to fetch all product variants but the user's wording restricts the candidate set to observed order items, follow the user's narrower scope.
- Before write tools, the new item id must be grounded in the current order observations or in the user's explicit requested catalog variant.
- Do not treat "user provided the order id" in a memory as mandatory. If the user has authenticated but does not know the order id, use current tools to retrieve the user's order list and inspect likely orders instead of stopping or repeatedly asking for the order id.
</custom_memory_applicability_guard>
