import json

import lark_oapi as lark
from lark_oapi.api.contact.v3 import *


def main():
    # 创建client
    client = lark.Client.builder() \
        .app_id("") \
        .app_secret("") \
        .log_level(lark.LogLevel.DEBUG) \
        .build()

    # 构造请求对象
    request: GetUserRequest = GetUserRequest.builder() \
        .user_id("") \
        .user_id_type("open_id") \
        .build()

    # 发起请求
    response: GetUserResponse = client.contact.v3.user.get(request)

    # 处理失败返回
    if not response.success():
        lark.logger.error(
            f"client.contact.v3.user.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
        return

    # 处理业务结果
    lark.logger.info(lark.JSON.marshal(response.data, indent=4))


if __name__ == "__main__":
    main()
