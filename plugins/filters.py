
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
        await message.reply_text(f"You are anonymous admin. Use /connect {message.chat.id} in PM")
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
            await message.reply_text("Make sure I'm present in your group!!", quote=True)
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

    # Check admin/owner
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

    # Parse command args
    if not message.text:
        await message.reply_text("Command Incomplete :(", quote=True)
        return

    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply_text("Command Incomplete :(", quote=True)
        return

    extracted = split_quotes(args[1])
    text = extracted[0].lower()

    # If no reply_to_message and no content provided in quotes
    if not message.reply_to_message and len(extracted) < 2:
        await message.reply_text("Add some content to save your filter!", quote=True)
        return

    # Initialize
    reply_text = ""
    btn = "[]"
    fileid = None
    alert = None

    # Case: content provided directly as argument (with possible button spec)
    if (len(extracted) >= 2) and not message.reply_to_message:
        reply_text, btn, alert = parser(extracted[1], text)
        if not reply_text:
            await message.reply_text("You cannot have buttons alone, give some text to go with it!", quote=True)
            return

    # Case: user replied to a message which contains inline buttons
    elif message.reply_to_message and message.reply_to_message.reply_markup:
        try:
            rm = message.reply_to_message.reply_markup
            btn = rm.inline_keyboard
            msg_file = get_file_id(message.reply_to_message)
            if msg_file:
                fileid = msg_file.file_id
                # prefer caption if present, else try text
                reply_text = message.reply_to_message.caption or ""
            else:
                reply_text = message.reply_to_message.text or ""
                fileid = None
            alert = None
        except Exception:
            reply_text = ""
            btn = "[]"
            fileid = None
            alert = None

    # Case: replied message has media (photo, video, sticker, etc.)
    elif message.reply_to_message and message.reply_to_message.media:
        try:
            msg_file = get_file_id(message.reply_to_message)
            fileid = msg_file.file_id if msg_file else None

            # For stickers, parser might not be needed — keep behavior similar to original:
            if message.reply_to_message.sticker:
                # use provided extracted[1] if available as button spec else keep caption
                if len(extracted) >= 2:
                    reply_text, btn, alert = parser(extracted[1], text)
                else:
                    reply_text = message.reply_to_message.caption or ""
                    btn = "[]"
                    alert = None
            else:
                # Non-sticker media — try caption with parser (if provided)
                # If caption exists and contains button spec, use parser on caption
                if message.reply_to_message.caption:
                    reply_text, btn, alert = parser(message.reply_to_message.caption, text)
                else:
                    reply_text = ""
                    btn = "[]"
                    alert = None
        except Exception:
            reply_text = ""
            btn = "[]"
            fileid = None
            alert = None

    # Case: replied message is plain text
    elif message.reply_to_message and message.reply_to_message.text:
        try:
            fileid = None
            reply_text, btn, alert = parser(message.reply_to_message.text, text)
        except Exception:
            reply_text = ""
            btn = "[]"
            alert = None

    else:
        # Unexpected case
        return

    # At this point we have: grp_id, text (keyword), reply_text, btn, fileid, alert
    # Save to DB. Ensure the signature of add_filter matches:
    # assumed: add_filter(grp_id, keyword, reply_text, buttons, file_id, alert)
    try:
        await add_filter(grp_id, text, reply_text, btn, fileid, alert)
    except Exception as e:
        await message.reply_text(f"Failed to add filter: {e}", quote=True)
        return

    sent_msg = await message.reply_text(
        f"Filter for {text} added in {title}",
        quote=True,
        parse_mode=enums.ParseMode.MARKDOWN
    )

    await asyncio.sleep(25)
    try:
        await sent_msg.delete()
    except Exception:
        pass


@Client.on_message(filters.command(['viewfilters', 'filters']) & filters.incoming)
async def get_all(client, message):
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

    texts = await get_filters(grp_id)
    count = await count_filters(grp_id)
    if count:
        filterlist = f"Total number of filters in **{title}** : {count}\n\n"
        for kw in texts:
            # texts assumed to be iterable of keywords or dict keys
            filterlist += " ×  `{}`\n".format(kw)

        if len(filterlist) > 4096:
            with io.BytesIO(str.encode(filterlist.replace("`", ""))) as keyword_file:
                keyword_file.name = "keywords.txt"
                await message.reply_document(
                    document=keyword_file,
                    quote=True
                )
            return
    else:
        filterlist = f"There are no active filters in **{title}**"

    await message.reply_text(
        text=filterlist,
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

    query = text.lower()
    # Adjust delete_filter signature if needed. Here assumed: delete_filter(grp_id, keyword)
    try:
        await delete_filter(grp_id, query)
        await message.reply_text(f"Deleted filter `{query}` from **{title}**", quote=True, parse_mode=enums.ParseMode.MARKDOWN)
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
       
