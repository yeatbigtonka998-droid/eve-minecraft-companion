package eve.skills;

import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Step;
import eve.skills.Plans.Stack;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.component.DataComponents;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.ItemStack;

import java.util.List;
import java.util.Map;

/**
 * withdraw{pos, item, count} / deposit{pos, item, count, allow_essentials}: move exactly {@code count} of one item between Eve's
 * main inventory and a container in reach. Shortages, a full side and other players' concurrent changes end as observed
 * counts, never as a claimed success. deposit keeps Eve's essentials (tools, weapons, armour, the last FOOD_RESERVE food)
 * unless the order says otherwise.
 */
final class Transfer extends Menus {
	static final int FOOD_RESERVE = 8;

	final boolean in; // true = withdraw (container -> Eve)
	final BlockPos pos;
	final String item;
	final int want;
	final boolean allowEssentials;
	Map<String, Integer> ownBefore, theirBefore;
	int phase; // 0 open, 1 snapshot, 2 move, 3 settle+verify
	boolean blocked; // the receiving side had no room

	Transfer(boolean withdraw, JsonObject p) {
		in = withdraw;
		pos = pos(p.getAsJsonObject("pos"));
		item = p.get("item").getAsString();
		want = p.get("count").getAsInt();
		allowEssentials = p.has("allow_essentials") && p.get("allow_essentials").getAsBoolean();
	}

	@Override
	public Step tick(BodyServices b) {
		bs = b;
		Minecraft mc = b.minecraft();
		LocalPlayer p = b.player();
		if (phase == 0) {
			Step s = open(b, pos);
			if (s != null) return s.done() ? s : Step.running();
			if (p.containerMenu == p.inventoryMenu || theirs(p).isEmpty()) return end(p, Step.failed("not_a_container", "no container menu opened"));
			phase = 1;
		}
		if (phase == 1) {
			if (!settled()) return Step.running(); // contents arrive with or right after the open
			ownBefore = Plans.totals(own(p));
			theirBefore = Plans.totals(theirs(p));
			b.evidence().add("container_before", json(theirBefore));
			if (!in) {
				String why = protectedReason(p);
				if (why != null) return end(p, Step.failed("protected_item", why));
			}
			phase = 2;
		}
		if (phase == 2) {
			if (move(mc, p)) {
				b.progress(Math.min(99, 100 * Math.max(0, received(p)) / want), null);
				return Step.running();
			}
			phase = 3;
			quiet = 0;
		}
		if (!settled()) return Step.running();
		return end(p, verify(b, p));
	}

	/** One click toward the order; false when there is nothing more to do (and nothing is carried). */
	boolean move(Minecraft mc, LocalPlayer p) {
		List<Stack> from = in ? theirs(p) : own(p), to = in ? own(p) : theirs(p);
		int moved = received(p);
		ItemStack carried = p.containerMenu.getCarried();
		if (!carried.isEmpty()) {
			Stack t = moved < want && item.equals(id(carried)) ? target(to) : null;
			if (t != null) click(mc, p, t.slot(), 1, ContainerInput.PICKUP);
			else {
				blocked |= moved < want && item.equals(id(carried));
				putBack(mc, p);
			}
			return true;
		}
		if (moved >= want || blocked) return false;
		Stack src = from.stream().filter(x -> item.equals(x.item())).min((a, c) -> Integer.compare(a.count(), c.count())).orElse(null);
		if (src == null) return false;
		int room = Plans.room(to, item, src.max());
		if (room == 0) {
			blocked = true;
			return false;
		}
		if (src.count() <= want - moved && src.count() <= room) click(mc, p, src.slot(), 0, ContainerInput.QUICK_MOVE);
		else { // partial stack: carry it, drop one at a time on the other side, put the rest back
			origin = src.slot();
			click(mc, p, src.slot(), 0, ContainerInput.PICKUP);
		}
		return true;
	}

	/** Items the receiving side gained so far; the cursor's stack is on neither side, so it never counts as moved. */
	int received(LocalPlayer p) {
		return in ? Plans.count(own(p), item) - ownBefore.getOrDefault(item, 0) : Plans.count(theirs(p), item) - theirBefore.getOrDefault(item, 0);
	}

	/** A slot on the receiving side that takes one more: an unfilled stack of the item first, else an empty slot. */
	Stack target(List<Stack> to) {
		return to.stream().filter(x -> item.equals(x.item()) && x.count() < x.max()).findFirst()
				.orElseGet(() -> to.stream().filter(x -> x.item() == null).findFirst().orElse(null));
	}

	String protectedReason(LocalPlayer p) {
		if (allowEssentials) return null;
		ItemStack s = own(p).stream().filter(x -> item.equals(x.item())).findFirst().map(x -> p.containerMenu.getSlot(x.slot()).getItem()).orElse(ItemStack.EMPTY);
		if (s.isEmpty()) return null; // nothing to deposit; verify reports the shortage
		if (s.has(DataComponents.TOOL) || s.has(DataComponents.WEAPON) || s.has(DataComponents.EQUIPPABLE))
			return item + " is one of Eve's tools/weapons/armour (allow_essentials not set)";
		if (s.has(DataComponents.FOOD)) {
			int food = 0;
			for (Stack x : own(p)) if (x.item() != null && p.containerMenu.getSlot(x.slot()).getItem().has(DataComponents.FOOD)) food += x.count();
			if (food - want < FOOD_RESERVE) return "would leave " + (food - want) + " food, reserve is " + FOOD_RESERVE + " (allow_essentials not set)";
		}
		return null;
	}

	Step verify(BodyServices b, LocalPlayer p) {
		Map<String, Integer> ownAfter = Plans.totals(own(p)), theirAfter = Plans.totals(theirs(p));
		int ownDelta = ownAfter.getOrDefault(item, 0) - ownBefore.getOrDefault(item, 0);
		int theirDelta = theirAfter.getOrDefault(item, 0) - theirBefore.getOrDefault(item, 0);
		int moved = in ? ownDelta : -ownDelta;
		JsonObject e = b.evidence();
		e.addProperty("requested", want);
		e.addProperty("moved", Math.max(0, moved));
		e.addProperty("short", Math.max(0, want - moved));
		e.add("container_after", json(theirAfter));
		e.add("inventory_delta", json(Plans.diff(ownBefore, ownAfter)));
		e.addProperty("foreign_change", ownDelta + theirDelta != 0); // someone else used the container meanwhile
		e.addProperty("clicks", clicks);
		if (moved == want) return Step.ok();
		if (moved > want) return Step.uncertain("moved " + moved + " of " + want);
		int had = in ? theirBefore.getOrDefault(item, 0) : ownBefore.getOrDefault(item, 0);
		if (blocked) return Step.failed(in ? "inventory_full" : "container_full", "moved " + moved + " of " + want);
		return Step.failed("insufficient", "moved " + moved + " of " + want + "; " + (in ? "container" : "inventory") + " had " + had);
	}

	Step end(LocalPlayer p, Step s) {
		close(p);
		return s;
	}
}
