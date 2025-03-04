import gymnasium as gym

from agents.random_agent import RandomAgent
from agents.actor_critic_agent import ActorCriticAgent
from agents.lqr_agent import LQRAgent


class AgentFactory:
    @staticmethod
    def create_agent(config, env):
            """
            Create an agent instance based on the provided configuration.
            The config dictionary is updated with the environment's state_space and action_space.
            """
            config["state_space"] = env.observation_space
            config["action_space"] = env.action_space

            agent_str = config.get("agent_str", "RANDOM").upper()

            if agent_str == "RANDOM":
                return RandomAgent(config)
            elif agent_str == "ACTOR-CRITIC":
                return ActorCriticAgent(config)
            elif agent_str == "LQR":
                return LQRAgent(config)
            elif agent_str == "LYAPUNOV":
                # Not implemented yet
                pass
            else:
                raise ValueError(f"Unknown agent type: {agent_str}")
