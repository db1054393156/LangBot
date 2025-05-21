from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *  # 导入事件类
import lark_oapi
from lark_oapi.api.im.v1 import *
import json
import uuid
# 注册插件
@register(name="MessageBeforePlugin", description="飞书发送消息前的提示消息", version="0.0.1", author="DengBo")
class MessageBeforePlugin(BasePlugin):

    # 插件加载时触发
    def __init__(self, host: APIHost):
        pass

    # 异步初始化
    async def initialize(self):
        pass

    # 当收到个人消息时触发
    @handler(PersonNormalMessageReceived)
    async def person_normal_message_received(self, ctx: EventContext):
        await self.send_before_message(ctx)

    # 当收到群消息时触发
    @handler(GroupNormalMessageReceived)
    async def group_normal_message_received(self, ctx: EventContext):
        await self.send_before_message(ctx)
    # @handler(NormalMessageResponded)
    # async def normal_message_responded(self, ctx: EventContext):
    #     print('normal_message_responded')
    #     ctx.prevent_default()

    async def send_before_message(self, ctx: EventContext):
        message_event = ctx.event.query.message_event
        # 获取源平台适配器
        adapter = ctx.event.query.adapter
        # 如果是飞书适配器
        if hasattr(adapter, 'api_client') and adapter.__class__.__name__ in ['LarkAdapter', 'LarkCardAdapter']:
            api_client = adapter.api_client
            # 获取消息链中的 Source 组件
            source = message_event.message_chain.source
            if source:
                # 获取消息事件对象
                payload_content = {
                    "config": {
                        "wide_screen_mode": True
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "content": "思考中",
                                "tag": "lark_md"
                            }
                        }
                    ]
                }
                request: ReplyMessageRequest = (
                    ReplyMessageRequest.builder()
                    .message_id(source.id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .content(json.dumps(payload_content))
                        .msg_type('interactive')
                        .reply_in_thread(False)
                        .uuid(str(uuid.uuid4()))
                        .build()
                    )
                    .build()
                )

                response: ReplyMessageResponse = await api_client.im.v1.message.areply(request)
                source.id = response.data.message_id
                if not response.success():
                    raise Exception(
                        f'client.im.v1.message.reply failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}'
                    )
    # 插件卸载时触发
    def __del__(self):
        pass
