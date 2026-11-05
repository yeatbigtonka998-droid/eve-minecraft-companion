package eve.body;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.entity.vehicle.boat.AbstractBoat;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/**
 * H-boat follow_boat (registry 0.15.4 draft): follow the owner across water by boat (Hunter: riding with him is "extremely
 * important for survival and companionship"). One action, the phases from BoatPlan:
 * LAUNCH: her boat item (pulled to the hotbar) is placed on the nearest water cell within 4 of her (vanilla BoatItem: a use in the
 * air while facing the water), then mounted (the mount primitive). SAIL: steer toward the owner, keep_min..keep_max behind (boat
 * keys only). LAND: the owner is off the water: steer to the nearest shore cell within 6 of the boat, dismount there (sneak).
 * RECOVER: break the boat (melee swings) and walk onto its drop. Ends ok on land with the boat back (or nothing to pick up).
 */
final class FollowBoat extends Skills.Act {
	final int owner;
	final double keepMin, keepMax;
	Skills.Act sub;
	int boatId = -1, ownerUnseen, launchTicks, recoverTicks;
	BlockPos waterCell, shoreCell;
	boolean placed, recovered, winding;
	String phaseNote;

	FollowBoat(String id, JsonObject params, long s, long d) {
		super("follow_boat", id, params, s, d);
		owner = params.get("entity_id").getAsInt();
		keepMin = params.has("keep_min") ? params.get("keep_min").getAsDouble() : 4;
		keepMax = params.has("keep_max") ? params.get("keep_max").getAsDouble() : 8;
	}

	/**
	 * Mind's note 1: a stop while she is afloat, or with her boat still out, lands and recovers first. True when it took the stop
	 * (she then ends cancelled once ashore with the boat back); false when there is nothing to wind down (the stop ends it now).
	 */
	boolean windDown(LocalPlayer p) {
		boolean afloat = p.getVehicle() instanceof AbstractBoat && !(p.getVehicle().getControllingPassenger() != null && p.getVehicle().getControllingPassenger() != p);
		if (!afloat && !(placed && boatId >= 0)) return false;
		winding = true;
		return true;
	}

	/** The arbiter reads sneak from the active action only, so this act wears its sub-act's. */
	static Boolean outerSneak(Skills.Act sub) {
		return sub == null ? null : sub.sneak;
	}

	static boolean boatItem(net.minecraft.world.item.ItemStack s) {
		String id = Hands.itemId(s);
		return id != null && id.endsWith("_boat") || id != null && id.endsWith("_raft"); // bamboo raft
	}

	static boolean onWater(Entity e) {
		return e.getVehicle() instanceof AbstractBoat || e.isInWater();
	}

	/** The water cell (surface: water with air above) at (x, z) on her feet level or one below. */
	static BlockPos waterAt(LocalPlayer p, int x, int z) {
		for (int dy : new int[] {0, -1}) {
			BlockPos b = BlockPos.containing(x, p.getY() + dy, z);
			if (p.level().getFluidState(b).is(net.minecraft.tags.FluidTags.WATER) && p.level().getBlockState(b.above()).isAir()) return b;
		}
		return null;
	}

	@Override
	Input tick(Minecraft mc, LocalPlayer p) {
		if (sub != null) { // a mount / dismount / attack in progress
			Input in = sub.tick(mc, p);
			sub.ticks++;
			sneak = outerSneak(sub); // S96 a115: the sub-act's sneak reaches the arbiter through this act (a dismount without it never left the boat)
			if (sub.status == null) return in;
			Skills.Act s = sub;
			sub = null;
			sneak = null;
			if ("mount".equals(s.skill) && !"ok".equals(s.status)) return done("failed", "unreachable", "could not get into the boat: " + s.code);
			return Input.EMPTY;
		}
		if (ticks == 0 && p.getVehicle() != null) { // mind's note 4: already riding (his 2-seat boat, a horse): no second boat
			Entity drv = p.getVehicle().getControllingPassenger();
			if (drv != null && drv != p || !(p.getVehicle() instanceof AbstractBoat)) return done("ok", null, "already riding; nothing to launch");
		}
		Entity o = mc.level.getEntity(owner);
		if (winding) o = null; // winding down: as if he had stepped ashore
		if (o == null && !winding) {
			if (++ownerUnseen > 400) return done("failed", "owner_gone", "the owner unseen for 20 s");
		} else ownerUnseen = 0;
		boolean ownerWater = !winding && o != null && onWater(o);
		Entity boat = boatId >= 0 ? mc.level.getEntity(boatId) : null;
		boolean inBoat = p.getVehicle() instanceof AbstractBoat;
		if (inBoat) boatId = p.getVehicle().getId();
		if (o == null && inBoat && !winding) return Input.EMPTY; // lost sight of him afloat: drift (owner_gone after 20 s)
		BoatPlan.Phase ph = BoatPlan.phase(inBoat, ownerWater, !inBoat && placed && boat != null && boat.isAlive());
		phase(ph.name().toLowerCase());
		switch (ph) {
			case LAUNCH -> {
				return launch(mc, p);
			}
			case SAIL -> {
				shoreCell = null;
				Entity v = p.getVehicle();
				BoatPlan.Keys k = BoatPlan.steer(v.getX(), v.getZ(), v.getYRot(), o.getX(), o.getZ(), keepMin, keepMax);
				return new Input(k.forward(), false, k.left(), k.right(), false, false, false);
			}
			case LAND -> {
				return land(mc, p);
			}
			case RECOVER -> {
				if (++recoverTicks > 400) return done("failed", "timeout", "could not break the boat in 20 s");
				if (!p.isWithinEntityInteractionRange(boat, 0)) return Skills.steer(p, boat.position()); // it drifted off the shore
				// S96 a129 (43 s in recover, no break): a bare-hand hit adds 10 to the boat's damage, which decays 1 a tick and breaks above
				// 40; one attack_entity per 15 ticks never got there. Hit at the cooldown instead (5 ticks), so it adds up.
				String prot = Interact.AttackEntity.protectedNear(mc, p, boat, false);
				if (prot != null) return done("failed", "protected_entity", "could not break the boat: " + prot);
				if (Hands.blockedByScreen(mc) == null && Hands.aim(p, boat.getBoundingBox().getCenter()) && Hands.crosshairOn(mc, boat)
						&& p.getAttackStrengthScale(0.5f) >= 1.0f) Hands.attack(mc);
				return Input.EMPTY;
			}
			default -> {
				// the boat broke: walk onto its drop for up to 3 s, then done
				for (Entity e : mc.level.entitiesForRendering())
					if (e instanceof ItemEntity it && boatItem(it.getItem()) && it.distanceTo(p) < 6 && recoverTicks < 460) {
						recoverTicks++;
						// S96 a131: the drop floats off into the water and steer stops at the bank (no ground ahead): water is safe to step into
						if (Reflexes.creeperRingBlocks(p, it.position(), false)) return Input.EMPTY; // O308: recoverTicks runs out, the boat stays
						Input in = Skills.steer(p, it.position());
						boolean aimed = Math.abs(Mth.wrapDegrees(Skills.yawPitch(p.position(), it.position())[0] - p.getYRot())) < 45f;
						return it.isInWater() && aimed ? new Input(true, false, false, false, p.horizontalCollision && p.isInWater(), false, false) : in;
					}
				// S96 a131: was "placed", so she reported a boat she had left floating
				for (int i = 0; i < 36 && !recovered; i++) recovered = boatItem(p.getInventory().getItem(i));
				if (winding) return done("cancelled", "cancelled", "stopped: landed and picked up the boat first");
				return done("ok", null, placed ? null : "the owner is ashore; no boat was needed");
			}
		}
	}

	private Input launch(Minecraft mc, LocalPlayer p) {
		if (placed) { // placed: mount it when it appears
			Entity boat = boatId >= 0 ? mc.level.getEntity(boatId) : null;
			if (boat == null) for (Entity e : mc.level.entitiesForRendering())
				if (e instanceof AbstractBoat b && b.getPassengers().isEmpty() && waterCell != null && b.distanceToSqr(Vec3.atCenterOf(waterCell)) < 9) {
					boat = b;
					boatId = b.getId();
				}
			if (boat == null) return ++launchTicks > 60 ? done("failed", "no_water", "the boat did not appear on the water") : Input.EMPTY;
			JsonObject prm = new JsonObject();
			prm.addProperty("entity_id", boatId);
			sub = new Interact.Mount(id + "-mount", prm, System.nanoTime(), System.nanoTime() + 10_000_000_000L);
			return Input.EMPTY;
		}
		int slot = -1;
		boolean has = false;
		for (int i = 0; i < 36 && slot < 0; i++)
			if (boatItem(p.getInventory().getItem(i))) {
				has = true;
				slot = Hands.hotbarSlotPulling(mc, p, Hands.itemId(p.getInventory().getItem(i)), null);
			}
		if (!has) return done("failed", "no_boat", "no boat in her inventory");
		if (slot < 0) {
			// S96 a121 (6 of 6 after the first launch): the note did not say which guard held, so it says now; a stray pause menu closes
			String screen = Hands.blockedByScreen(mc);
			if (++launchTicks <= 60) return Input.EMPTY;
			return done("failed", "timeout", "could not reach the boat in her inventory: screen " + screen + ", menu " + p.containerMenu.getClass().getSimpleName() + ", carried " + Hands.itemId(p.containerMenu.getCarried()));
		} // S96 a113 stop_afloat#4: not no_boat
		if (waterCell == null) {
			// S96 rig a113 (5 of 5: the boat never appeared): vanilla's BoatItem refuses a boat whose box touches a block, and the box is
			// wider than a cell, so the cell next to the shore fails; launch on one with water all around it, within her reach
			int bx = p.getBlockX(), bz = p.getBlockZ();
			int[] c = BoatPlan.nearest(8, (dx, dz) -> BoatPlan.clearOfShore(dx, dz, (x, z) -> waterAt(p, bx + x, bz + z) != null));
			if (c == null) return done("failed", "no_water", "no open water within 8 to launch from");
			waterCell = waterAt(p, bx + c[0], bz + c[1]);
		}
		if (!p.isWithinBlockInteractionRange(waterCell, 0)) { // walk up to it, into the shallows if she must; 5 s, then no_water
			if (++launchTicks > 100) return done("failed", "no_water", "could not get within reach of open water at " + waterCell.toShortString());
			return Skills.steer(p, Vec3.atCenterOf(waterCell));
		}
		p.getInventory().setSelectedSlot(slot);
		if (!Hands.aim(p, Vec3.atCenterOf(waterCell).add(0, 0.45, 0))) return Input.EMPTY;
		mc.hitResult = BlockHitResult.miss(p.getEyePosition(), Direction.UP, p.blockPosition()); // a use in the air: BoatItem ray-traces the water
		Hands.use(mc);
		mc.options.keyUse.setDown(false);
		placed = true;
		launchTicks = 0; // the wait for the boat starts now
		evidence.addProperty("boat_placed", waterCell.toShortString());
		return Input.EMPTY;
	}

	private double landBest = Double.MAX_VALUE;
	private int landStuck;

	private Input land(Minecraft mc, LocalPlayer p) {
		Entity v = p.getVehicle();
		if (shoreCell == null || ticks % 20 == 0) { // re-picked as she moves: it closes on the shore nearest the owner (S96 a115: she landed 10+ off)
			int bx = v.getBlockX(), bz = v.getBlockZ();
			Entity o = winding ? null : mc.level.getEntity(owner);
			int[] c = BoatPlan.nearestTo(6, (dx, dz) -> shore(p, bx + dx, bz + dz) != null, o == null ? 0 : o.getBlockX() - bx, o == null ? 0 : o.getBlockZ() - bz);
			if (c == null) {
				if (++launchTicks > 400) return done("failed", "unreachable", "no shore within 6 of the boat for 20 s");
				return Input.EMPTY; // drift; the owner may come back to the water
			}
			shoreCell = shore(p, bx + c[0], bz + c[1]);
		}
		// S96 a129/a131: the cell nearest him lies inland, the bank stops the boat and it slides along (never "still"): land within 2 of the
		// boat and he is in the band, or no nearer the target for 1 s: get out here. a121: 10 s no nearer and no land near: a failure, not a wait.
		Vec3 target = Vec3.atBottomCenterOf(shoreCell);
		double gap = Math.hypot(target.x - v.getX(), target.z - v.getZ());
		if (gap < landBest - 0.3) {
			landBest = gap;
			landStuck = 0;
		} else landStuck++;
		Entity him = winding ? null : mc.level.getEntity(owner);
		int vx = v.getBlockX(), vz = v.getBlockZ();
		int[] near = BoatPlan.nearest(2, (dx, dz) -> shore(p, vx + dx, vz + dz) != null);
		if (near != null && (landStuck > 20 || him != null && him.distanceTo(v) <= keepMax)) {
			shoreCell = shore(p, vx + near[0], vz + near[1]);
			sub = new Interact.Dismount(id + "-out", new JsonObject(), System.nanoTime(), System.nanoTime() + 3_000_000_000L);
			evidence.addProperty("landed_at", shoreCell.toShortString());
			return Input.EMPTY;
		}
		if (landStuck > 200) return done("failed", "unreachable", "the boat came no nearer the shore at " + shoreCell.toShortString() + " for 10 s");
		Vec3 s = Vec3.atBottomCenterOf(shoreCell);
		if (Math.hypot(s.x - v.getX(), s.z - v.getZ()) < 1.8) { // alongside: get out (sneak)
			sub = new Interact.Dismount(id + "-out", new JsonObject(), System.nanoTime(), System.nanoTime() + 3_000_000_000L);
			evidence.addProperty("landed_at", shoreCell.toShortString());
			return Input.EMPTY;
		}
		BoatPlan.Keys k = BoatPlan.steer(v.getX(), v.getZ(), v.getYRot(), s.x, s.z, 0, 0.5);
		return new Input(k.forward(), false, k.left(), k.right(), false, false, false);
	}

	/** A shore cell: walkable footing (solid below, two of air) next to water, around the boat's level. */
	static BlockPos shore(LocalPlayer p, int x, int z) {
		var l = p.level();
		for (int dy : new int[] {0, 1, -1}) {
			BlockPos f = BlockPos.containing(x, p.getVehicle() != null ? p.getVehicle().getY() + dy : p.getY() + dy, z);
			if (!l.getBlockState(f.below()).getCollisionShape(l, f.below()).isEmpty() && l.getBlockState(f).isAir() && l.getBlockState(f.above()).isAir()
					&& l.getFluidState(f.below()).isEmpty()) return f;
		}
		return null;
	}

	private void phase(String ph) {
		if (!ph.equals(phaseNote)) {
			phaseNote = ph;
			note = ph;
			lastProgressNs = 0; // the arbiter sends action.progress with the new phase at once
		}
	}

	@Override
	boolean resumable() {
		return true;
	}

	@Override
	void cancel(Minecraft mc, LocalPlayer p) {
		if (sub != null) sub.cancel(mc, p);
	}

	@Override
	void fill(Minecraft mc, LocalPlayer p) {
		Entity o = mc.level.getEntity(owner);
		if (!evidence.has("boat_placed")) evidence.add("boat_placed", null);
		evidence.addProperty("mounted", boatId >= 0);
		if (!evidence.has("landed_at")) evidence.add("landed_at", null);
		evidence.addProperty("boat_recovered", recovered);
		evidence.addProperty("distance_to_owner", o == null ? null : Skills.r2(o.distanceTo(p)));
		evidence.addProperty("phase", phaseNote);
	}
}
