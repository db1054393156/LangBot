from __future__ import annotations

import lark_oapi
from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody
import traceback
import typing
import asyncio
import re
import base64
import json
import datetime
import hashlib
from Crypto.Cipher import AES

import aiohttp
import lark_oapi.ws.exception
import quart
from lark_oapi.api.im.v1 import *

from .. import adapter
# from ...core import app
from ..types import message as platform_message
from ..types import events as platform_events
from ..types import entities as platform_entities

from ...core import app, entities as core_entities, taskmgr


class AESCipher(object):
    def __init__(self, key):
        self.bs = AES.block_size
        self.key = hashlib.sha256(AESCipher.str_to_bytes(key)).digest()

    @staticmethod
    def str_to_bytes(data):
        u_type = type(b''.decode('utf8'))
        if isinstance(data, u_type):
            return data.encode('utf8')
        return data

    @staticmethod
    def _unpad(s):
        return s[: -ord(s[len(s) - 1 :])]

    def decrypt(self, enc):
        iv = enc[: AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self._unpad(cipher.decrypt(enc[AES.block_size :]))

    def decrypt_string(self, enc):
        enc = base64.b64decode(enc)
        return self.decrypt(enc).decode('utf8')


# class Card(platform_message.MessageComponent):
#     """飞书卡片消息组件"""
#
#     type: str = 'template'
#     """消息组件类型。"""
#     content: str
#     """卡片消息内容，JSON 格式。"""
#
#     def __str__(self):
#         return "[卡片消息]"


class LarkCardMessageConverter(adapter.MessageConverter):
    @staticmethod
    async def yiri2target(
            message_chain: platform_message.MessageChain, api_client: lark_oapi.Client
    ) -> []:
        # message_elements = []
        pending_paragraph = []
        for msg in message_chain:
            if isinstance(msg, platform_message.Plain):
                # Ensure text is valid UTF-8
                try:
                    text = msg.text.encode('utf-8').decode('utf-8')
                    pending_paragraph.append({'tag': 'markdown', 'content': text})
                except UnicodeError:
                    # If text is not valid UTF-8, try to decode with other encodings
                    try:
                        text = msg.text.encode('latin1').decode('content-8')
                        pending_paragraph.append({'tag': 'markdown', 'text': text})
                    except UnicodeError:
                        # If still fails, replace invalid characters
                        text = msg.text.encode('utf-8', errors='replace').decode('utf-8')
                        pending_paragraph.append({'tag': 'markdown', 'content': text})
            elif isinstance(msg, platform_message.At):
                # pending_paragraph.append({'tag': 'at', 'user_id': msg.target, 'style': []})
                pass
            elif isinstance(msg, platform_message.AtAll):
                # pending_paragraph.append({'tag': 'at', 'user_id': 'all', 'style': []})
                pass
            elif isinstance(msg, platform_message.Image):
                image_bytes = None

                if msg.base64:
                    try:
                        # Remove data URL prefix if present
                        if msg.base64.startswith('data:'):
                            msg.base64 = msg.base64.split(',', 1)[1]
                        image_bytes = base64.b64decode(msg.base64)
                    except Exception:
                        traceback.print_exc()
                        continue
                elif msg.url:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(msg.url) as response:
                                if response.status == 200:
                                    image_bytes = await response.read()
                                else:
                                    traceback.print_exc()
                                    continue
                    except Exception:
                        traceback.print_exc()
                        continue
                elif msg.path:
                    try:
                        with open(msg.path, 'rb') as f:
                            image_bytes = f.read()
                    except Exception:
                        traceback.print_exc()
                        continue

                if image_bytes is None:
                    continue

                try:
                    # Create a temporary file to store the image bytes
                    import tempfile

                    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                        temp_file.write(image_bytes)
                        temp_file.flush()

                        # Create image request using the temporary file
                        request = (
                            CreateImageRequest.builder()
                            .request_body(
                                CreateImageRequestBody.builder()
                                .image_type('message')
                                .image(open(temp_file.name, 'rb'))
                                .build()
                            )
                            .build()
                        )

                        response = await api_client.im.v1.image.acreate(request)

                        if not response.success():
                            raise Exception(
                                f'client.im.v1.image.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}'
                            )

                        image_key = response.data.image_key

                        # message_elements.append(pending_paragraph)
                        pending_paragraph.append(
                            # [
                                {
                                    'tag': 'img',
                                    'img_key': image_key,
                                }
                            # ]
                        )
                        # pending_paragraph = []
                except Exception:
                    traceback.print_exc()
                    continue
                finally:
                    # Clean up the temporary file
                    import os

                    if 'temp_file' in locals():
                        os.unlink(temp_file.name)
            elif isinstance(msg, platform_message.Forward):
                for node in msg.node_list:
                    pending_paragraph.extend(await LarkCardMessageConverter.yiri2target(node.message_chain, api_client))

        # if pending_paragraph:
        #     message_elements.append(pending_paragraph)
        return pending_paragraph

    @staticmethod
    async def target2yiri(
            message: lark_oapi.api.im.v1.model.event_message.EventMessage,
            api_client: lark_oapi.Client,
    ) -> platform_message.MessageChain:
        message_content = json.loads(message.content)

        lb_msg_list = []

        msg_create_time = datetime.datetime.fromtimestamp(int(message.create_time) / 1000)

        lb_msg_list.append(platform_message.Source(id=message.message_id, time=msg_create_time))

        if message.message_type == 'text':
            element_list = []

            def text_element_recur(text_ele: dict) -> list[dict]:
                if text_ele['text'] == '':
                    return []

                at_pattern = re.compile(r'@_user_[\d]+')
                at_matches = at_pattern.findall(text_ele['text'])

                name_mapping = {}
                for mathc in at_matches:
                    for mention in message.mentions:
                        if mention.key == mathc:
                            name_mapping[mathc] = mention.name
                            break

                if len(name_mapping.keys()) == 0:
                    return [text_ele]

                # 只处理第一个，剩下的递归处理
                text_split = text_ele['text'].split(list(name_mapping.keys())[0])

                new_list = []

                left_text = text_split[0]
                right_text = text_split[1]

                new_list.extend(text_element_recur({'tag': 'text', 'text': left_text, 'style': []}))

                new_list.append(
                    {
                        'tag': 'at',
                        'user_id': list(name_mapping.keys())[0],
                        'user_name': name_mapping[list(name_mapping.keys())[0]],
                        'style': [],
                    }
                )

                new_list.extend(text_element_recur({'tag': 'text', 'text': right_text, 'style': []}))

                return new_list

            element_list = text_element_recur({'tag': 'text', 'text': message_content['text'], 'style': []})

            message_content = {'title': '', 'content': element_list}

        elif message.message_type == 'post':
            new_list = []

            for ele in message_content['content']:
                if type(ele) is dict:
                    new_list.append(ele)
                elif type(ele) is list:
                    new_list.extend(ele)

            message_content['content'] = new_list
        elif message.message_type == 'image':
            message_content['content'] = [{'tag': 'img', 'image_key': message_content['image_key'], 'style': []}]

        for ele in message_content['content']:
            if ele['tag'] == 'text':
                lb_msg_list.append(platform_message.Plain(text=ele['text']))
            elif ele['tag'] == 'at':
                lb_msg_list.append(platform_message.At(target=ele['user_name']))
            elif ele['tag'] == 'img':
                image_key = ele['image_key']

                request: GetMessageResourceRequest = (
                    GetMessageResourceRequest.builder()
                    .message_id(message.message_id)
                    .file_key(image_key)
                    .type('image')
                    .build()
                )

                response: GetMessageResourceResponse = await api_client.im.v1.message_resource.aget(request)

                if not response.success():
                    raise Exception(
                        f'client.im.v1.message_resource.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}'
                    )

                image_bytes = response.file.read()
                image_base64 = base64.b64encode(image_bytes).decode()

                image_format = response.raw.headers['content-type']

                lb_msg_list.append(platform_message.Image(base64=f'data:{image_format};base64,{image_base64}'))

        return platform_message.MessageChain(lb_msg_list)


class LarkCardEventConverter(adapter.EventConverter):
    @staticmethod
    async def yiri2target(
        event: platform_events.MessageEvent,
    ) -> lark_oapi.im.v1.P2ImMessageReceiveV1:
        pass

    @staticmethod
    async def target2yiri(
        event: lark_oapi.im.v1.P2ImMessageReceiveV1, api_client: lark_oapi.Client
    ) -> platform_events.Event:
        message_chain = await LarkCardMessageConverter.target2yiri(event.event.message, api_client)

        if event.event.message.chat_type == 'p2p':
            return platform_events.FriendMessage(
                sender=platform_entities.Friend(
                    id=event.event.sender.sender_id.open_id,
                    nickname=event.event.sender.sender_id.union_id,
                    remark='',
                ),
                message_chain=message_chain,
                time=event.event.message.create_time,
            )
        elif event.event.message.chat_type == 'group':
            return platform_events.GroupMessage(
                sender=platform_entities.GroupMember(
                    id=event.event.sender.sender_id.open_id,
                    member_name=event.event.sender.sender_id.union_id,
                    permission=platform_entities.Permission.Member,
                    group=platform_entities.Group(
                        id=event.event.message.chat_id,
                        name='',
                        permission=platform_entities.Permission.Member,
                    ),
                    special_title='',
                    join_timestamp=0,
                    last_speak_timestamp=0,
                    mute_time_remaining=0,
                ),
                message_chain=message_chain,
                time=event.event.message.create_time,
            )


class SessionQuery:
    def __init__(self, launcher_type, launcher_id):
        self.launcher_type = launcher_type
        self.launcher_id = launcher_id

class LarkCardAdapter(adapter.MessagePlatformAdapter):
    bot: lark_oapi.ws.Client
    api_client: lark_oapi.Client

    bot_account_id: str  # 用于在流水线中识别at是否是本bot，直接以bot_name作为标识
    lark_tenant_key: str  # 飞书企业key

    message_converter: LarkCardMessageConverter = LarkCardMessageConverter()
    event_converter: LarkCardEventConverter = LarkCardEventConverter()

    listeners: typing.Dict[
        typing.Type[platform_events.Event],
        typing.Callable[[platform_events.Event, adapter.MessagePlatformAdapter], None],
    ] = {}

    config: dict
    quart_app: quart.Quart
    ap: app.Application
    card_update_supported: bool = True  # 支持更新卡片

    def __init__(self, config: dict, ap: app.Application):
        self.config = config
        self.ap = ap
        self.quart_app = quart.Quart(__name__)

        @self.quart_app.route('/lark_card/callback', methods=['POST'])
        async def lark_callback():
            try:
                data = await quart.request.json

                self.ap.logger.debug(f'Lark card callback event: {data}')

                if 'encrypt' in data:
                    cipher = AESCipher(self.config['encrypt-key'])
                    data = cipher.decrypt_string(data['encrypt'])
                    data = json.loads(data)

                type = data.get('type')
                if type is None:
                    context = EventContext(data)
                    type = context.header.event_type

                if 'url_verification' == type:
                    # todo 验证verification token
                    return {'challenge': data.get('challenge')}
                context = EventContext(data)
                type = context.header.event_type

                if 'card.action.trigger' == type:
                    if context.event['action']['value']['action']!='reset':
                        return {'action': data.get('action')}
                    is_group = context.event['action']['value']['chat_type'] == 'group'
                    launcher_type = core_entities.LauncherTypes.GROUP if is_group else core_entities.LauncherTypes.PERSON
                    launcher_id = context.event['context']['open_chat_id'] if is_group else context.event['operator']['open_id']
                    # 判断群聊还是私聊
                    query = SessionQuery(launcher_type, launcher_id)
                    session = await self.ap.sess_mgr.get_session(query)
                    session.using_conversation = None
                    payload_content = {
                        "config": {
                            "wide_screen_mode": True
                        },
                        "elements": [{"tag":"column_set","horizontal_spacing":"8px","horizontal_align":"left","columns":[{"tag":"column","width":"weighted","elements":[{"tag":"hr","margin":"0px 0px 0px 0px"}],"padding":"0px 0px 0px 0px","direction":"vertical","horizontal_spacing":"8px","vertical_spacing":"8px","horizontal_align":"center","vertical_align":"center","margin":"0px 0px 0px 0px","weight":1},{"tag":"column","width":"weighted","elements":[{"tag":"div","text":{"tag":"plain_text","content":"对话结束","text_size":"normal_v2","text_align":"center","text_color":"default"},"margin":"0px 0px 0px 0px"}],"vertical_spacing":"8px","horizontal_align":"left","vertical_align":"top","weight":1},{"tag":"column","width":"weighted","elements":[{"tag":"hr","margin":"0px 0px 0px 0px"}],"padding":"0px 0px 0px 0px","direction":"vertical","horizontal_spacing":"8px","vertical_spacing":"8px","horizontal_align":"center","vertical_align":"center","margin":"0px 0px 0px 0px","weight":1}],"margin":"0px 0px 0px 0px"}]
                    }
                    request: CreateMessageRequest = (
                        CreateMessageRequest.builder()
                        .receive_id_type("chat_id")
                        .request_body(
                            CreateMessageRequestBody.builder()
                            .receive_id(context.event['context']['open_chat_id'])
                            .content(json.dumps(payload_content))
                            .msg_type('interactive')
                            .build()
                        )
                        .build()
                    )

                    self.api_client.im.v1.message.create(request)
                    return {'action': data.get('action')}
                p2v1 = P2ImMessageReceiveV1()
                p2v1.header = context.header
                event = P2ImMessageReceiveV1Data()
                event.message = EventMessage(context.event['message'])
                event.sender = EventSender(context.event['sender'])
                p2v1.event = event
                p2v1.schema = context.schema
                if 'im.message.receive_v1' == type:
                    try:
                        event = await self.event_converter.target2yiri(p2v1, self.api_client)
                    except Exception:
                        traceback.print_exc()

                    if event.__class__ in self.listeners:
                        await self.listeners[event.__class__](event, self)

                return {'code': 200, 'message': 'ok'}
            except Exception:
                traceback.print_exc()
                return {'code': 500, 'message': 'error'}

        async def on_message(event: lark_oapi.im.v1.P2ImMessageReceiveV1):
            lb_event = await self.event_converter.target2yiri(event, self.api_client)

            await self.listeners[type(lb_event)](lb_event, self)

        def sync_on_message(event: lark_oapi.im.v1.P2ImMessageReceiveV1):
            asyncio.create_task(on_message(event))

        event_handler = (
            lark_oapi.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(sync_on_message).build()
        )

        self.bot_account_id = config['bot_name']

        self.bot = lark_oapi.ws.Client(config['app_id'], config['app_secret'], event_handler=event_handler)
        self.api_client = lark_oapi.Client.builder().app_id(config['app_id']).app_secret(config['app_secret']).build()

    async def send_message(self, target_type: str, target_id: str, message: platform_message.MessageChain):
        pass

    async def reply_message(
        self,
        message_source: platform_events.MessageEvent,
        message: platform_message.MessageChain,
        quote_origin: bool = False,
    ):
        lark_message = await self.message_converter.yiri2target(message, self.api_client)
        lark_message.append({
                "tag": "column_set",
                "horizontal_align": "left",
                "columns": [
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {
                                    "tag": "plain_text",
                                    "content": "结束对话"
                                },
                                "type": "default",
                                "width": "default",
                                "size": "medium",
                                "behaviors": [
                                    {
                                        "type": "callback",
                                        "value": {
                                            "action": "reset",
                                            "chat_type": 'p2p' if message_source.type=='FriendMessage' else 'group'
                                        }
                                    }
                                ]
                            }
                        ],
                        "direction": "horizontal",
                        "vertical_spacing": "8px",
                        "horizontal_align": "left",
                        "vertical_align": "top"
                    }
                ]
            })
        payload_content={
            "config": {
                "wide_screen_mode": True
            },
            "elements": lark_message
        }
        request = (
            PatchMessageRequest.builder()
            .message_id(message_source.message_chain.message_id)
            .request_body(
                PatchMessageRequestBody.builder().content(json.dumps(payload_content)).build()

            )
            .build()
        )
        response = await self.api_client.im.v1.message.acreate(request)
        if not response.success():
            raise Exception(
                f'client.im.v1.message.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}, content: \n{json.dumps(payload_content, indent=4, ensure_ascii=False)}'
            )

    async def is_muted(self, group_id: int) -> bool:
        return False

    def register_listener(
        self,
        event_type: typing.Type[platform_events.Event],
        callback: typing.Callable[[platform_events.Event, adapter.MessagePlatformAdapter], None],
    ):
        self.listeners[event_type] = callback

    def unregister_listener(
        self,
        event_type: typing.Type[platform_events.Event],
        callback: typing.Callable[[platform_events.Event, adapter.MessagePlatformAdapter], None],
    ):
        self.listeners.pop(event_type)

    async def run_async(self):
        port = self.config['port']
        enable_webhook = self.config['enable-webhook']

        if not enable_webhook:
            try:
                await self.bot._connect()
            except lark_oapi.ws.exception.ClientException as e:
                raise e
            except Exception as e:
                await self.bot._disconnect()
                if self.bot._auto_reconnect:
                    await self.bot._reconnect()
                else:
                    raise e
        else:

            async def shutdown_trigger_placeholder():
                while True:
                    await asyncio.sleep(1)

            await self.quart_app.run_task(
                host='0.0.0.0',
                port=port,
                shutdown_trigger=shutdown_trigger_placeholder,
            )

    async def kill(self) -> bool:
        return False 