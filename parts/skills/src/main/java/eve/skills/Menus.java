package eve.skills;

import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import eve.skills.Plans.Stack;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Shared base for skills that work an open menu (container, crafting table, furnace, the inventory's own 2x2 grid). Clicks go
 * through vanilla's {@code handleContainerInput}, at most one per tick, and every decision is re-read from the menu the next
 * tick, so a server correction (someone else took the items, a full container) changes the plan instead of being ignored.
 * Results come from the slot contents after the server's sync, never from "clicked".
 */
abstract class Menus implements Skill {
	static final int SETTLE_TICKS = 6; // server slot sync after the last click (loopback ~1-2 ticks; margin for a busy server)

	/** This tick's body services (set first thing in every tick). */
	BodyServices bs;
	SubAction opening;
	boolean opened;
	int quiet; // ticks since the last click
	int clicks;
	/** Menu slot the carried stack was picked up from; put back there when the plan changes. */
	int origin = -1;

	/** Opens the block's menu (left open for us); null while waiting, a terminal step on failure, Step "ok" marker once open. */
	Step open(BodyServices b, BlockPos pos) {
		bs = b;
		if (opened) return null;
		if (opening == null) opening = b.useBlock(pos);
		Step s = opening.tick();
		if (!s.done()) return s;
		if (!"ok".equals(s.status())) return s; // the child's code: zone_denied (private / outside zones), out_of_range, obstructed...
		opened = true;
		quiet = 0;
		return null;
	}

	static String id(ItemStack s) {
		return s.isEmpty() ? null : BuiltInRegistries.ITEM.getKey(s.getItem()).toString();
	}

	/** Player main inventory (hotbar + 27) as it appears in this menu; armour/offhand excluded. */
	static List<Stack> own(LocalPlayer p) {
		List<Stack> out = new ArrayList<>();
		AbstractContainerMenu m = p.containerMenu;
		for (int i = 0; i < m.slots.size(); i++) {
			Slot s = m.slots.get(i);
			if (s.container == p.getInventory() && s.getContainerSlot() < 36) out.add(stack(i, s));
		}
		return out;
	}

	/** The block's own slots (chest/barrel/furnace), excluding the player's. */
	static List<Stack> theirs(LocalPlayer p) {
		List<Stack> out = new ArrayList<>();
		AbstractContainerMenu m = p.containerMenu;
		for (int i = 0; i < m.slots.size(); i++) {
			Slot s = m.slots.get(i);
			if (s.container != p.getInventory()) out.add(stack(i, s));
		}
		return out;
	}

	static Stack stack(int menuSlot, Slot s) {
		ItemStack it = s.getItem();
		return new Stack(menuSlot, id(it), it.getCount(), it.isEmpty() ? s.getMaxStackSize() : Math.min(s.getMaxStackSize(it), it.getMaxStackSize()));
	}

	void click(Minecraft mc, LocalPlayer p, int slot, int button, ContainerInput how) {
		bs.clickSlot(slot, button, how); // body API v2: vanilla handleContainerInput, the same packet as a mouse click
		clicks++;
		quiet = 0;
	}

	/**
	 * Forces a full inventory resync from the server. Slot updates the server sends while it still thinks a block menu is open
	 * (the close packet in flight) carry that menu's id and the client drops them, so the client can hold a stale inventory
	 * (seen on lane B: a fixture `give` right after a crafting-table close). Those updates were counted against the block
	 * menu, so the inventory menu's state ids still agree and a plain click changes nothing (lane B attempt 15). So the
	 * client's own state id is bumped first: the no-op click then always carries a mismatch, and the 26.3 server answers with
	 * broadcastFullState (ServerGamePacketListenerImpl.handleContainerClick), the whole inventory as the server holds it.
	 * Block menus need none of this: opening one sends the full contents.
	 */
	static void resync(BodyServices b) {
		LocalPlayer p = b.player();
		if (p == null || p.containerMenu != p.inventoryMenu || !p.containerMenu.getCarried().isEmpty()) return;
		if (p.inventoryMenu.getSlot(0).hasItem()) return; // the 2x2 result is live: a click there would craft
		p.containerMenu.incrementStateId(); // client-side counter only: guarantees the server sees a stale state id
		// the inventory's own crafting result slot (menu slot 0) with an empty grid and an empty cursor: a click that can never
		// change anything, whether the inventory is full or not (a pick-up-and-put-back raced the full resend: lane B a19)
		b.clickSlot(0, 0, ContainerInput.PICKUP);
	}

	/** True once no click happened for SETTLE_TICKS (counts this tick). */
	boolean settled() {
		return ++quiet > SETTLE_TICKS;
	}

	/**
	 * One step of "put {@code item} into menu slot {@code to} until it holds {@code want}", using the carried stack: pick up from a
	 * player slot, right-click one at a time, put the rest back. Returns false when nothing is left to do (target full or no
	 * source), with nothing carried.
	 */
	boolean feed(Minecraft mc, LocalPlayer p, int to, String item, int want) {
		AbstractContainerMenu m = p.containerMenu;
		ItemStack carried = m.getCarried();
		ItemStack there = m.getSlot(to).getItem();
		boolean need = there.getCount() < want && (there.isEmpty() || item.equals(id(there)));
		if (!carried.isEmpty()) {
			if (need && item.equals(id(carried))) click(mc, p, to, 1, ContainerInput.PICKUP);
			else putBack(mc, p);
			return true;
		}
		if (!need) return false;
		Stack src = own(p).stream().filter(x -> item.equals(x.item())).min((a, c) -> Integer.compare(a.count(), c.count())).orElse(null);
		if (src == null) return false;
		origin = src.slot();
		click(mc, p, src.slot(), 0, ContainerInput.PICKUP);
		return true;
	}

	/** Drops the carried stack back where it came from, or into any player slot that takes it. */
	void putBack(Minecraft mc, LocalPlayer p) {
		ItemStack c = p.containerMenu.getCarried();
		int to = origin;
		if (to < 0 || !accepts(p.containerMenu.getSlot(to), c))
			to = own(p).stream().filter(x -> x.item() == null || x.item().equals(id(c)) && x.count() < x.max()).mapToInt(Stack::slot).findFirst().orElse(-1);
		if (to >= 0) click(mc, p, to, 0, ContainerInput.PICKUP);
		origin = -1;
	}

	static boolean accepts(Slot s, ItemStack c) {
		ItemStack h = s.getItem();
		return s.mayPlace(c) && (h.isEmpty() || ItemStack.isSameItemSameComponents(h, c) && h.getCount() < h.getMaxStackSize());
	}

	@Override
	public void cancel(BodyServices b) {
		b.closeMenu(); // the server hands back grid items on close (the body also closes on every end)
	}

	void close(LocalPlayer p) {
		bs.closeMenu();
	}

	static JsonObject json(Map<String, Integer> m) {
		JsonObject o = new JsonObject();
		m.forEach(o::addProperty);
		return o;
	}

	static BlockPos pos(JsonObject o) {
		return new BlockPos(o.get("x").getAsInt(), o.get("y").getAsInt(), o.get("z").getAsInt());
	}
}
