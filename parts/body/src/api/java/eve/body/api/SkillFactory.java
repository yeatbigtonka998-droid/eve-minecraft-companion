package eve.body.api;

/** One new {@link Skill} per accepted action.request; params are already validated against the registry schema. */
@FunctionalInterface
public interface SkillFactory {
	Skill create(BodyServices body);
}
