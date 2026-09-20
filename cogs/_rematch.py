"""所有遊戲共用的「再來一局」按鈕視圖。

每個遊戲都把自己的「啟動一局」邏輯包成一個 callback，
RematchView 會在玩家按下按鈕時呼叫該 callback、自動處理雙倍下注。

callback 的呼叫方式：
    await callback(interaction, amount)
其中 interaction 已經是按鈕的 interaction（response 仍未使用），
amount 是新一局的下注金額。

callback 內部要自行用 interaction.followup.send() 等方式送訊息，
因為按鈕的 interaction 在 RematchView 內部會先被 edit_message 消耗。
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

import config
from database import db


PlayCallback = Callable[[discord.Interaction, int], Awaitable[None]]


async def try_defer(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = False,
    thinking: bool = False,
) -> bool:
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except discord.NotFound:
        return False


async def safe_view_edit(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
    message: discord.Message | None = None,
) -> bool:
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
        return True
    except (discord.HTTPException, discord.NotFound):
        if message is not None:
            try:
                await message.edit(embed=embed, view=view)
                return True
            except discord.HTTPException:
                pass
        return False


async def send_initial(
    interaction: discord.Interaction,
    embed: discord.Embed,
    view: discord.ui.View | None = None,
    file: discord.File | None = None,
    *,
    is_followup: bool = False,
) -> discord.Message:
    """送出某一局的初始訊息，回傳 Message 物件供後續編輯動畫。"""
    kwargs = {"embed": embed}
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file

    if is_followup or interaction.response.is_done():
        try:
            return await interaction.followup.send(**kwargs, wait=True)
        except discord.NotFound:
            if interaction.channel and hasattr(interaction.channel, "send"):
                return await interaction.channel.send(**kwargs)
            raise

    try:
        await interaction.response.send_message(**kwargs)
        return await interaction.original_response()
    except discord.NotFound:
        if interaction.channel and hasattr(interaction.channel, "send"):
            return await interaction.channel.send(**kwargs)
        raise


async def send_error(
    interaction: discord.Interaction, msg: str, *, is_followup: bool = False
) -> None:
    if is_followup or interaction.response.is_done():
        try:
            await interaction.followup.send(msg, ephemeral=True)
            return
        except discord.NotFound:
            if interaction.channel and hasattr(interaction.channel, "send"):
                await interaction.channel.send(msg)
                return
            raise

    try:
        await interaction.response.send_message(msg, ephemeral=True)
    except discord.NotFound:
        if interaction.channel and hasattr(interaction.channel, "send"):
            await interaction.channel.send(msg)
            return
        raise


async def refund_failed_start(
    interaction: discord.Interaction,
    amount: int,
    *,
    pending_bet_id: str | None = None,
    restore_items: bool = True,
    message: str = "⚠️ 遊戲開局失敗，下注已自動退回。請再試一次。",
) -> None:
    """扣款後若 Discord 回應失敗，退回下注，避免玩家餘額被卡住。"""
    await db.cancel_pending_bet(
        interaction.user.id,
        pending_bet_id,
        amount,
        restore_items=restore_items,
    )
    try:
        await send_error(interaction, message, is_followup=True)
    except discord.DiscordException:
        pass


class RematchView(discord.ui.View):
    """提供「再來一局」與「雙倍再來」兩顆按鈕。"""

    def __init__(
        self,
        player_id: int,
        last_bet: int,
        callback: PlayCallback,
        *,
        timeout: float = 300.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.player_id = player_id
        self.last_bet = last_bet
        self.callback_fn = callback
        self.used = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message(
                "❌ 這不是你的遊戲！", ephemeral=True
            )
            return False
        if self.used:
            await interaction.response.send_message(
                "這個按鈕已經被用過了，請重新使用 `/<指令>` 開新一局。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="🔄 再來一局", style=discord.ButtonStyle.primary, row=4)
    async def replay(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._do_replay(interaction, 1)

    @discord.ui.button(
        label="💰 雙倍再來", style=discord.ButtonStyle.success, row=4
    )
    async def replay_double(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._do_replay(interaction, 2)

    async def _do_replay(
        self, interaction: discord.Interaction, multiplier: int
    ) -> None:
        if not await try_defer(interaction):
            return
        new_amount = self.last_bet * multiplier
        if new_amount > config.MAX_GAMBLE_BET:
            await send_error(
                interaction,
                f"❌ 下注上限為 **{config.MAX_GAMBLE_BET:,}**，無法用這個按鈕開局。",
                is_followup=True,
            )
            return
        balance = await db.get_balance(interaction.user.id)
        if balance < new_amount:
            await send_error(
                interaction,
                f"❌ 餘額不足！需要 **{new_amount:,}**，"
                f"目前餘額 **{balance:,}**",
                is_followup=True,
            )
            return

        self.used = True
        for child in self.children:
            child.disabled = True
        try:
            await interaction.edit_original_response(view=self)
        except discord.HTTPException:
            pass

        await self.callback_fn(interaction, new_amount)
