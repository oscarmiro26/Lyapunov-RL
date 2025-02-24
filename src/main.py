import gymnasium as gym
import numpy as np

from agents.agent_factory import AgentFactory
from util.metrics_tracker import MetricsTracker


def show_one_episode(env_str: str, config: dict):
    env = gym.make(env_str, render_mode="human")
    agent = AgentFactory.create_agent(config=config, env=env)
    agent.load()

    done = False
    obs, _ = env.reset() 
    while not done:
        action = agent.policy(obs)
        obs, reward, terminated, truncated, _ = env.step(action)
        done = terminated or truncated

    env.close()


def run_episode(env_str: str, config: dict, num_episodes: int):
    env = gym.make(env_str)
    agent = AgentFactory.create_agent(config=config, env=env)

    episode_returns = []
    episode_actor_losses = []
    episode_critic_losses = []

    for episode in range(num_episodes):
        ep_return = 0.0
        ep_actor_losses = []
        ep_critic_losses = []
        done = False
        obs, _ = env.reset() 

        while not done:
            old_obs = obs
            action = agent.policy(old_obs)
            obs, reward, terminated, truncated, _ = env.step(action)

            ep_return += reward
            done = terminated or truncated

            agent.add_transition((old_obs, action, reward, obs, done))

        if len(agent._replay_buffer) > 0:
            loss = agent.update()
            if loss:
                actor_loss, critic_loss = loss
                ep_actor_losses.append(actor_loss)
                ep_critic_losses.append(critic_loss)

        episode_returns.append(ep_return)
        avg_actor_loss = np.mean(ep_actor_losses) if ep_actor_losses else 0.0
        avg_critic_loss = np.mean(ep_critic_losses) if ep_critic_losses else 0.0
        episode_actor_losses.append(avg_actor_loss)
        episode_critic_losses.append(avg_critic_loss)

        if (episode + 1) % 10 == 0:
            print(f"Episode {episode+1}/{num_episodes} | Return: {ep_return:.2f} "
                  f"| Actor Loss: {avg_actor_loss:.4f} | Critic Loss: {avg_critic_loss:.4f}")

    if config.get("save_models"):
        agent.save()

    if config.get("show_one_episode", False):
        show_one_episode(env_str, config)

    env.close()
    return episode_returns, episode_actor_losses, episode_critic_losses


def train_agent(env_str: str, config: dict, tracker: MetricsTracker, num_runs: int, num_episodes: int):
    if config["agent_str"] == "LQR":
        agent_name = "LQR"
    else:
        agent_name = f'{config["agent_str"]}_lr{config["actor_lr"]}_{config["critic_lr"]}_gamma{config["gamma"]}_n{config["n_steps"]}'
    
    print(agent_name)

    for run in range(num_runs):
        print(f"Starting run {run+1}/{num_runs}...")
        returns, actor_losses, critic_losses = run_episode(env_str, config, num_episodes)
        tracker.add_run_returns(agent_id=agent_name, returns=returns)
        tracker.add_run_losses(agent_id=agent_name, actor_losses=actor_losses, critic_losses=critic_losses)
 
def main():
    env_str = "Pendulum-v1"
    config_ac = {
        "agent_str": "ACTOR-CRITIC",
        "actor_lr": 0.0005,
        "critic_lr": 0.001,
        "gamma": 0.8,
        "n_steps": 10,
        "save_models": False,
    }
    config_lqr = {
        "agent_str": "LQR",
        "save_models": False
    }
    num_runs = 1
    num_episodes = 500

    tracker = MetricsTracker()

    train_agent(env_str, config_ac, tracker, num_runs, num_episodes)
    
    tracker.plot_split()

if __name__ == "__main__":
    main()
