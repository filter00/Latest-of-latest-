import io
import asyncio
from pyrogram import filters, Client, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.filters_mdb import (
    add_filter,
    get_filters,
    delete_filter,
    count_filters
)

from database.connections_mdb import active_connection
from utils import get_file_id, parser, split_quotes
from info import ADMINS


async def _resolve_group(client, message):
    """
    Helper: returns (grp_id, title, userid) or (None, None, None) on failure.
    Sends reply messages for common failure cases.
    """
    userid = message.from_user.id if message.from_user else None
    if not userid:
        await message.reply_text(
            f"You are anonymous admin. Use /connect {message.chat.id} in PM"
        )
        return None, None, None

    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        grpid = await active_connection(str(userid))
        if not grpid:
            await message.reply_text("I'm not connected to any groups!", quote=True)
            return None, None, None
        try:
            chat = await client.get_chat(grpid)
            title = chat.title
            return grpid, title, userid
        except Exception:
            await message.reply_text("Unable to resolve connected group!", quote=True)
            return None, None, None

    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return message.chat.id, message.chat.title, userid

    else:
        return None, None, None


@Client.on_message(filters.command(['filter', 'add']) & filters.incoming)
async def addfilter(client, message):
    """
    Handles adding a filter:
    - Either /filter <keyword> "<reply with optional button spec>" OR
    - Reply to any message with: /filter <keyword>
    """

    grp_id, title, userid = await _resolve_group(client, message)
    if not grp_id:
        return

    try:
        st = await client.get_chat_member(grp_id, userid)
    except Exception:
        return

    if (
        st.status != enums.ChatMemberStatus.ADMINISTRATOR
        and st.status != enums.ChatMemberStatus.OWNER
        and str(userid) not in ADMINS
    ):
        return

    # Parse arguments
    is_reply = False
    replied = message.reply_to_message
    if replied:
        is_reply = True

    if len(message.text.split()) < 2:
        await message.reply_text(
            "Usage:\n"
            "`/filter keyword reply text`\n\n"
            "Or reply to a message with:\n"
            "`/filter keyword`",
            quote=True,
            parse_mode=enums.ParseMode.MARKDOWN,
        )
        return

    # Split out keyword and (optional) text/buttons
    try:
        cmd, *rest = message.text.split(" ", 1)
        if not rest:
            await message.reply_text(
                "Provide a keyword along with reply, or reply to a message.",
                quote=True,
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return
        text = rest[0]
    except Exception:
        await message.reply_text(
            "Provide a keyword along with reply, or reply to a message.",
            quote=True,
            parse_mode=enums.ParseMode.MARKDOWN,
        )
        return

    # If using quotes + button parser
    keyword = None
    reply_text = None
    btn = None
    fileid = None
    alert = None

    if is_reply:
        # /filter keyword as reply to message
        parts = text.split(None, 1)
        if len(parts) < 1:
            await message.reply_text(
                "You must specify a keyword!",
                quote=True
            )
            return
        keyword = parts[0].strip().lower()

        # The replied message content becomes the reply_text
        if replied.text:
            reply_text = replied.text
        elif replied.caption:
            reply_text = replied.caption
        else:
            reply_text = ""

        fileid = get_file_id(replied)

    else:
        # Normal usage: /filter keyword reply
        # We'll allow quotes+buttons or just plain text
        extracted = split_quotes(text)
        if isinstance(extracted, (list, tuple)) and len(extracted) >= 2:
            # keyword, reply(+buttons)
            keyword = extracted[0].lower()
            parsed = parser(extracted[1])

            # parser may return (reply, btn) or (reply, btn, alert)
            if isinstance(parsed, (list, tuple)):
                if len(parsed) == 3:
                    reply_text, btn, alert = parsed
                elif len(parsed) == 2:
                    reply_text, btn = parsed
                elif len(parsed) == 1:
                    reply_text = parsed[0]
            else:
                reply_text = parsed
        else:
            # no quotes, simple space split
            parts = text.split(None, 1)
            if len(parts) < 2:
                await message.reply_text(
                    "Usage: `/filter keyword reply text`",
                    quote=True,
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
                return
            keyword = parts[0].lower()
            reply_text = parts[1]

    if not keyword:
        await message.reply_text("Keyword parse issue. Try again.", quote=True)
        return

    if reply_text is None:
        await message.reply_text("Reply text missing. Try again.", quote=True)
        return

    # store filter in DB (filters_mdb.add_filter expects: grp_id, text, reply_text, btn, file, alert)
    await add_filter(grp_id, keyword, reply_text, btn, fileid, alert)

    await message.reply_text(
        f"Added filter `{keyword}` in **{title}**",
        quote=True,
        parse_mode=enums.ParseMode.MARKDOWN,
    )


@Client.on_message(filters.command('viewfilters') & filters.incoming)
async def get_all(_, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return

    chat_type = message.chat.type

    if chat_type == enums.ChatType.PRIVATE:
        grp_id = await active_connection(str(userid))
        if not grp_id:
            await message.reply_text("I'm not connected to any groups!", quote=True)
            return
        title = (await _.get_chat(grp_id)).title
    else:
        grp_id = message.chat.id
        title = message.chat.title

    filters_list = await get_filters(grp_id)
    count = await count_filters(grp_id)

    if not count:
        await message.reply_text(f"There are no filters in **{title}**", quote=True)
        return

    msg = f"Total {count} filters in **{title}**:\n"

    # get_filters returns a list of text strings
    for text in filters_list:
        msg += f"• `{text}`\n"

    await message.reply_text(
        msg,
        quote=True,
        parse_mode=enums.ParseMode.MARKDOWN
    )


@Client.on_message(filters.command('del') & filters.incoming)
async def deletefilter(client, message):
    grp_id, title, userid = await _resolve_group(client, message)
    if not grp_id:
        return

    try:
        st = await client.get_chat_member(grp_id, userid)
    except Exception:
        return
    if (
        st.status != enums.ChatMemberStatus.ADMINISTRATOR
        and st.status != enums.ChatMemberStatus.OWNER
        and str(userid) not in ADMINS
    ):
        return

    # Command parsing
    try:
        cmd, text = message.text.split(" ", 1)
    except Exception:
        await message.reply_text(
            "<i>Mention the filtername which you wanna delete!</i>\n\n"
            "<code>/del filtername</code>\n\n"
            "Use /viewfilters to view all available filters",
            quote=True
        )
        return

    query = text.strip().lower()
    if not query:
        await message.reply_text("Filter name khali mat chhodo!", quote=True)
        return

    try:
        # filters_mdb.delete_filter expects: (message, text, group_id)
        await delete_filter(message, query, grp_id)
        # Success / not-found ka reply DB wala function khud bhej deta hai
    except Exception as e:
        await message.reply_text(f"Failed to delete filter: {e}", quote=True)


@Client.on_message(filters.command('delall') & filters.incoming)
async def delallconfirm(client, message):
    grp_id, title, userid = await _resolve_group(client, message)
    if not grp_id:
        return

    try:
        st = await client.get_chat_member(grp_id, userid)
    except Exception:
        return

    if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
        await message.reply_text(
            f"This will delete all filters from '{title}'.\nDo you want to continue??",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(text="YES", callback_data="delallconfirm")],
                [InlineKeyboardButton(text="CANCEL", callback_data="delallcancel")]
            ]),
            quote=True
        )
