import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

with patch.dict(os.environ, {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "history_paging_tests"}):
    import server


class CustomerHistoryPagingTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_pages_beyond_100_orders_with_stable_scope_and_exact_total(self):
        customer = {"id": "customer", "business_id": "business", "pos_external_id": "source-customer", "order_history": [{"order": "saved"}]}
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.to_list = AsyncMock(return_value=[{"id": "order-151"}])
        orders = SimpleNamespace(find=MagicMock(return_value=cursor), count_documents=AsyncMock(return_value=201))
        with patch.multiple(server,
            db=SimpleNamespace(pos_customers=SimpleNamespace(find_one=AsyncMock(return_value=customer)), pos_sales_orders=orders),
            get_current_user=AsyncMock(return_value={"id": "actor"}),
            pos_read_filter=AsyncMock(side_effect=[{"business_id": "business"}, {"business_id": "business", "outlet_id": "permitted"}]),
            validate_pos_admin_business=AsyncMock(), require_business_module_enabled=AsyncMock()):
            result = await server.get_customer_order_history("customer", None, limit=50, offset=150)
        self.assertEqual(result["pagination"], {"limit": 50, "offset": 150, "total": 201, "has_more": True})
        self.assertEqual(result["saved_history"], customer["order_history"])
        query = orders.find.call_args.args[0]
        self.assertEqual(query["$and"][0], {"business_id": "business", "outlet_id": "permitted"})
        self.assertIn({"metadata.customerId": "source-customer"}, query["$and"][1]["$or"])
        orders.count_documents.assert_awaited_once_with(query)
        cursor.skip.assert_called_once_with(150)
        cursor.to_list.assert_awaited_once_with(50)
