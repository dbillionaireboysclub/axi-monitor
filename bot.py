async def scan():
    log.info("="*40 + " SCAN START")
    try:
        rows = await fetch_vt_data()
    except Exception as e:
        log.error(f"Fetch failed: {e}")
        await tg(f"❌ *VT Markets Monitor Error*\n\n`{e}`")
        return

    this_month, last_month, curr_label, prev_label = process(rows)
    all_members = this_month + last_month
    flagged = [c for c in all_members if c["flagged"]]

    log.info(f"This month: {len(this_month)} | Last month: {len(last_month)} | Flagged: {len(flagged)}")

    # Send withdrawal alerts
    for c in flagged:
        pct = (c['withs'] / c['deps'] * 100) if c['deps'] > 0 else 0
        await tg(
            f"⚠️ *WITHDRAWAL ALERT*\n\n"
            f"👤 *{c['name']}*\n"
            f"🆔 `{c['user_id']}`\n"
            f"🌍 {c['country']}\n"
            f"📅 Registered: {c['reg']} _{c['period']}_\n\n"
            f"💰 Deposited: *${c['deps']:,.2f}*\n"
            f"📤 Withdrawn: *${c['withs']:,.2f}*\n"
            f"📊 Net: *${c['net']:,.2f}*\n\n"
            f"🔴 *{pct:.1f}%* of capital withdrawn"
        )
        await asyncio.sleep(0.5)

    # Daily summary
    curr_deps  = sum(c['deps']  for c in this_month)
    curr_withs = sum(c['withs'] for c in this_month)
    prev_deps  = sum(c['deps']  for c in last_month)
    prev_withs = sum(c['withs'] for c in last_month)

    await tg(
        f"📋 *VT Markets — Daily Report*\n\n"
        f"📅 *{curr_label}*\n"
        f"👥 Members: *{len(this_month)}*\n"
        f"💰 Deposited: *${curr_deps:,.2f}*\n"
        f"📤 Withdrawn: *${curr_withs:,.2f}*\n\n"
        f"📅 *{prev_label}*\n"
        f"👥 Members: *{len(last_month)}*\n"
        f"💰 Deposited: *${prev_deps:,.2f}*\n"
        f"📤 Withdrawn: *${prev_withs:,.2f}*\n\n"
        f"⚠️ Total flagged: *{len(flagged)}*" if flagged else
        f"📋 *VT Markets — Daily Report*\n\n"
        f"📅 *{curr_label}*\n"
        f"👥 Members: *{len(this_month)}*\n"
        f"💰 Deposited: *${curr_deps:,.2f}*\n"
        f"📤 Withdrawn: *${curr_withs:,.2f}*\n\n"
        f"📅 *{prev_label}*\n"
        f"👥 Members: *{len(last_month)}*\n"
        f"💰 Deposited: *${prev_deps:,.2f}*\n"
        f"📤 Withdrawn: *${prev_withs:,.2f}*\n\n"
        f"All clear. 🟢"
    )

    log.info("Scan complete.")
