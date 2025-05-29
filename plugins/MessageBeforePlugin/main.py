from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *  # 导入事件类
import lark_oapi
from lark_oapi.api.im.v1 import *
from lark_oapi.api.cardkit.v1 import *
import json
import uuid
import traceback
import aiohttp
import base64
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
        await self.send_before_message(ctx,'FriendMessage')

    # 当收到群消息时触发
    @handler(GroupNormalMessageReceived)
    async def group_normal_message_received(self, ctx: EventContext):
        await self.send_before_message(ctx,'GroupMessage')
    
    @handler(NormalMessageResponded)
    async def normal_message_responded(self, ctx: EventContext):
        query = ctx.event.query
        # 获取回复的消息文本
        response_text = ctx.event.response_text
        # 记录原始回复（例如用于日志）
        card_data = ctx.event.query.get_variable("card_data")
        sequence = ctx.event.query.get_variable("sequence")
        # 从query中获取card_data
        if card_data:
            card_data = ctx.event.query.get_variable("card_data")
            # 使用card_data进行处理
            if hasattr(query, 'adapter') and query.adapter.__class__.__name__ in ['LarkAdapter', 'LarkCardAdapter']:
                api_client = query.adapter.api_client
                sequence_new= sequence + 1
                # 更新卡片内容
                try:
                    if response_text == "END-对话结束-END":
                        end_button=[{
                            "behaviors":[
                                {
                                    "type": "callback",
                                    "value": {
                                        "action": "reset",
                                        "chat_type": 'p2p'  # if message_type == 'FriendMessage' else 'group'
                                    }
                                }
                            ],
                            "element_id":"button_1",
                            "size":"medium",
                            "tag":"button",
                            "text":{
                                "content":"结束对话",
                                "tag":"plain_text"
                            },
                            "type":"default",
                            "width":"default"
                        }]
                        request: CreateCardElementRequest = (
                            CreateCardElementRequest.builder()
                            .card_id(card_data['card_id'])
                            .request_body(
                                CreateCardElementRequestBody.builder()
                                .type('append')
                                .uuid(str(uuid.uuid1()))
                                .sequence(sequence_new)
                                .elements(json.dumps(end_button))
                                .build()
                            ).build()
                        )
                        response = await api_client.cardkit.v1.card_element.acreate(request)
                        if not response.success():
                            raise Exception(
                                f"client.cardkit.v1.card.card_element failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")

                        cardConfig={
                            "config": {
                                "streaming_mode": False,
                                "summary": {
                                    "content": "已回答"
                                },
                            },
                        }
                        requestSetting: SettingsCardRequest = (
                            SettingsCardRequest.builder()
                            .card_id(card_data['card_id']).request_body(
                                SettingsCardRequestBody.builder()
                                .settings(json.dumps(cardConfig))
                                .uuid(str(uuid.uuid1()))
                                .sequence(sequence_new+1)
                                .build())
                            .build()
                        )
                        response_card: SettingsCardResponse = await api_client.cardkit.v1.card.asettings(requestSetting)
                        if not response_card.success():
                            raise Exception(
                                f"client.cardkit.v1.card_settings.content failed, code: {response_card.code}, msg: {response_card.msg}, log_id: {response_card.get_log_id()}, resp: \n{json.dumps(json.loads(response_card.raw.content), indent=4, ensure_ascii=False)}")
                    else:
                        # 构建更新卡片的请求
                        lark_message = []
                        if query.resp_messages and len(query.resp_messages) > 0:
                            # 获取最新的响应消息
                            latest_message = query.resp_messages[-1]
                            # 使用get_content_platform_message_chain方法获取平台消息链
                            if hasattr(latest_message, 'get_content_platform_message_chain'):
                                platform_message_chain = latest_message.get_content_platform_message_chain()
                                # 然后使用转换方法
                                lark_message = await query.adapter.message_converter.yiri2target(platform_message_chain,api_client)
                        if lark_message:
                            filtered_data = [item for item in lark_message if item['tag'] == 'markdown']
                            if filtered_data:
                                request: ContentCardElementRequest = (
                                    ContentCardElementRequest.builder()
                                    .card_id(card_data['card_id'])
                                    .element_id(card_data['element_id'])
                                    .request_body(
                                        ContentCardElementRequestBody.builder()
                                        .uuid(str(uuid.uuid1()))
                                        .content(filtered_data[0]['content'])
                                        .sequence(sequence_new)
                                        .build())
                                    .build()
                                )
                                # 发起更新卡片请求
                                response =  await api_client.cardkit.v1.card_element.acontent(request)
                                # 处理失败返回
                                if not response.success():
                                    raise Exception(
                                        f"client.cardkit.v1.card_element.content failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")

                        if lark_message:
                            filtered_data = [item for item in lark_message if item['tag'] != 'markdown']
                            if filtered_data:
                                sequence_new=sequence_new+1
                                request: CreateCardElementRequest = (
                                    CreateCardElementRequest.builder()
                                    .card_id(card_data['card_id'])
                                    .request_body(
                                        CreateCardElementRequestBody.builder()
                                        .type('append')
                                        .uuid(str(uuid.uuid1()))
                                        .sequence(sequence_new)
                                        .elements(json.dumps(filtered_data))
                                        .build()
                                    ).build()
                                )
                                response_image: CreateCardElementResponse = await api_client.cardkit.v1.card_element.acreate(request)
                                if not response_image.success():
                                    raise Exception(
                                        f"client.cardkit.v1.card.card_element failed, code: {response_image.code}, msg: {response_image.msg}, log_id: {response_image.get_log_id()}, resp: \n{json.dumps(json.loads(response_image.raw.content), indent=4, ensure_ascii=False)}")
                        ctx.event.query.set_variable("sequence", sequence_new)

                except Exception as e:
                    print(f"更新卡片时发生异常: {str(e)}")
                ctx.prevent_default()
        if response_text == "END-对话结束-END":
            ctx.prevent_default()


    async def send_before_message(self, ctx: EventContext,message_type):
        message_event = ctx.event.query.message_event
        # 获取源平台适配器
        adapter = ctx.event.query.adapter
        # 如果是飞书适配器
        if hasattr(adapter, 'api_client') and adapter.__class__.__name__ in ['LarkAdapter', 'LarkCardAdapter']:
            api_client = adapter.api_client
            # 获取消息链中的 Source 组件
            source = message_event.message_chain.source
            if source:
                element_id="markdown_1"
                card_info={
                    "body":{
                        "elements":[
                            {
                                "content":"思考中",
                                "element_id":element_id,
                                "tag":"markdown"
                            }
                        ]
                    },
                    "config":{
                        "streaming_mode":True,
                        "summary":{
                            "content": "思考中"
                        },
                        "streaming_config":{
                            "print_strategy":"delay"
                        }
                    },
                    "schema":"2.0"
                }
                request: CreateCardRequest = (
                    CreateCardRequest.builder()
                    .request_body(
                        CreateCardRequestBody.builder()
                        .type("card_json")
                        .data(json.dumps(card_info))
                        .build())
                    .build()
                )
                # 发起请求
                response_card =  await api_client.cardkit.v1.card.acreate(request)
                # 处理失败返回
                if not response_card.success():
                    raise Exception(
                        f"client.cardkit.v1.card.create failed, code: {response_card.code}, msg: {response_card.msg}, log_id: {response_card.get_log_id()}, resp: \n{json.dumps(json.loads(response_card.raw.content), indent=4, ensure_ascii=False)}")
                payload_content={
                    "type": "card",
                    "data": {
                        "card_id": response_card.data.card_id
                    }
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
                # 构建卡片数据
                card_data = {
                    "card_id": response_card.data.card_id,
                    "element_id": element_id,
                }

                ctx.event.query.set_variable("card_data", card_data)
                ctx.event.query.set_variable("sequence", 0)

    # 插件卸载时触发
    def __del__(self):
        pass
