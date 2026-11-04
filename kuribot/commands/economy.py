"""Economy commands: packs, shop, market."""

from __future__ import annotations

from discord.ext import commands

from kuribot.core import bot
from kuribot.database import db
from kuribot.shops import get_card_shop_offers, get_equip_shop_offers, get_monster_shop_offers
from kuribot.pack_opening import PACK_MIGRATION_NOTICE_KEY, ticket_to_pack_banner_notice_text
from kuribot.render.shop import make_market_hub_file
from kuribot.ui.shop import MonsterShopView
from kuribot.ui.market import MarketHubView
from kuribot.ui.packs import make_pack_inventory_file, PacksView


@bot.command(name="shop")
async def shop_cmd(ctx: commands.Context):
    db.get_user(ctx.author.id)
    get_monster_shop_offers(ctx.author.id)
    get_card_shop_offers(ctx.author.id)
    get_equip_shop_offers(ctx.author.id)
    view = MonsterShopView(ctx.author.id, ctx.author.display_name)
    embed, file = view.build_message()
    if file is None:
        await ctx.send(embed=embed, view=view)
    else:
        await ctx.send(embed=embed, file=file, view=view)


@bot.command(name="market")
async def market_cmd(ctx: commands.Context):
    user = db.get_user(ctx.author.id)
    file = make_market_hub_file(ctx.author.display_name, user)
    await ctx.send(file=file, view=MarketHubView(ctx.author.id, ctx.author.display_name))


@bot.command(name="packs", aliases=["pack", "banner"])
async def packs_cmd(ctx: commands.Context):
    db.get_user(ctx.author.id)
    invoked = str(getattr(ctx, "invoked_with", "") or "").casefold()
    if invoked == "banner" and db.mark_notice_shown_once(ctx.author.id, PACK_MIGRATION_NOTICE_KEY):
        await ctx.send(ticket_to_pack_banner_notice_text())
    file = make_pack_inventory_file(ctx.author.display_name, ctx.author.id)
    view = PacksView(ctx.author.id, ctx.author.display_name)
    view.message = await ctx.send(file=file, view=view)
