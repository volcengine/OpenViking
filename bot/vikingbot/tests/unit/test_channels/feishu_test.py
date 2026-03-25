import asyncio

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    GetChatRequest
)
import json

from vikingbot.channels.feishu import FeishuChannel

async def feishu_channel():
    print("Testing feishu channel")
    client = (
        lark.Client.builder()
        .app_id("")
        .app_secret("")
        .log_level(lark.LogLevel.INFO)
        .build()
    )
    chat_id = "oc_656f04c0485140eeb60414f6bcf56927"
    # 构造请求对象
    request: GetChatRequest = GetChatRequest.builder() \
        .chat_id(chat_id) \
        .user_id_type("open_id") \
        .build()

    # 发起请求
    response = await client.im.v1.chat.aget(request)

    # 处理失败返回
    if not response.success():
        print(
            f"client.im.v1.chat.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
        return

    # 处理业务结果
    data = response.data

    print(lark.JSON.marshal(response.data, indent=4))

if __name__ == '__main__':
    asyncio.run(feishu_channel())
