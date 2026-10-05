"""未处理异常返回固定 JSON，HTTPException 保持原样。"""

import asyncio
import json
import unittest

from fastapi import HTTPException

from app.errors import unhandled_exception


class ErrorTests(unittest.TestCase):
    def test_unhandled_exception_hides_detail(self):
        with self.assertLogs("panel", level="ERROR"):
            response = asyncio.run(unhandled_exception(None, RuntimeError("boom")))
        self.assertEqual(response.status_code, 500)
        payload = json.loads(response.body.decode())
        self.assertEqual(payload, {"detail": "服务器内部错误"})
        self.assertNotIn("boom", response.body.decode())

    def test_http_exception_is_not_replaced(self):
        response = asyncio.run(unhandled_exception(None, HTTPException(status_code=401, detail="未登录或令牌缺失")))
        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body.decode())
        self.assertEqual(payload["detail"], "未登录或令牌缺失")


if __name__ == "__main__":
    unittest.main()
