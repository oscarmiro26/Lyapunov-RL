import os
import torch
import numpy as np
import dreal as d

from agents.las_lyapunov_agent import LAS_LyapunovAgent
from util.metrics_tracker import MetricsTracker
from util.logger_utils import setup_run_directory_and_logging
from util.dynamics import pendulum_dynamics_torch, pendulum_dynamics_dreal

def extract_ce_from_model(model, state_dim):
    if not model:
        return np.zeros(state_dim)
    ce = np.zeros(state_dim)
    for var, val in model.items():
        var_name = str(var)
        if var_name.startswith('x'):
            try:
                i = int(var_name[1:])
                if 0 <= i < state_dim:
                    ce[i] = val.mid() if isinstance(val, d.Interval) else val
            except (ValueError, IndexError):
                continue
    return ce


def run_las_lyapunov_ac_cegar_training():
    config = {
        "model_name": "LAS_LyapunovAC_CEGAR",
        "max_action": 1.0,
        "beta": 0.6,
        "dynamics_fn_dreal": pendulum_dynamics_dreal,
        "dynamics_fn": pendulum_dynamics_torch,
        "LQR": {
            "agent_str": "LQR",
            "environment": "InvertedPendulum",
            "discrete_discounted": False,
            "g": 9.81,
            "m": 0.15,
            "l": 0.5,
            "b": 0.1,
            "max_action": 1.0,
            "state_space": np.zeros(2),
            "action_space": np.zeros(1),
        },
        "alpha": 0.2,
        "lr": 2e-3,
        "batch_size": 256,
        "num_paths_sampled": 8,
        "norm_threshold": 5e-2,
        "integ_threshold": 500,
        "dt": 0.003,
        "actor_hidden_sizes": (5, 5), 
        "critic_hidden_sizes": (20, 20),
        "state_space": np.zeros(2), 
        "action_space": np.zeros(1), 
        "r1_bounds": (np.array([-2.0, -4.0]), np.array([2.0, 4.0])),
    }
    
    tracker = MetricsTracker()
    run_dir, logger = setup_run_directory_and_logging(config)

    model_name = config["model_name"]
    config["run_dir"] = run_dir

    agent = LAS_LyapunovAgent(config)

    MAX_CEGAR_ITERATIONS = 50
    TRAINING_STEPS_PER_ITERATION = 1000
    CERTIFICATION_LEVEL_C = 0.5
    
    all_counter_examples = []
    total_actor_losses = []
    total_critic_losses = []

    logger.info("Starting CEGAR Training Loop...")
    logger.info(f"Initial LQR c* used by blending function: {agent.blending_function.c_star:.4f}")
    for i in range(MAX_CEGAR_ITERATIONS):
        logger.info(f"CEGAR Iteration {i + 1}/{MAX_CEGAR_ITERATIONS}")
        
        # LEARNER PHASE
        logger.info(f"Starting Learner Phase ({TRAINING_STEPS_PER_ITERATION} steps)...") 
        logger.info(f"Current number of counter-examples: {len(all_counter_examples)}")
        
        for step in range(TRAINING_STEPS_PER_ITERATION):
            actor_loss, critic_loss = agent.update(counter_examples=all_counter_examples)

            total_actor_losses.append(actor_loss)
            total_critic_losses.append(critic_loss)

            if (step + 1) % 10 == 0:
                logger.info(f"  Step {step+1}: Actor Loss={actor_loss:.4f}, Critic Loss={critic_loss:.4f}")

        # FALSIFIER PHASE
        logger.info(f"Starting Falsifier Phase for composite system at c = {CERTIFICATION_LEVEL_C}...")
        
        is_verified, ce_model = agent.trainer.check_lyapunov_with_ce(
            level=CERTIFICATION_LEVEL_C, eps=0.05
        )

        if is_verified:
            logger.info(f"\nSUCCESS! Composite system verified for c = {CERTIFICATION_LEVEL_C} at CEGAR iteration {i + 1}.")
            agent.save(file_path=run_dir, episode=(i + 1) * TRAINING_STEPS_PER_ITERATION)
            break
        else:
            new_ce = extract_ce_from_model(ce_model, config["state_space"].shape[0])
            logger.info(new_ce)
            logger.info(f"Falsifier found counter-example: {np.round(new_ce, 4)}. Adding to training set.")
            all_counter_examples.append(new_ce)
            agent.save(file_path=run_dir, episode=(i + 1) * TRAINING_STEPS_PER_ITERATION)

        logger.info(f"Model saved to {run_dir}")

    else:
        logger.error("\nTRAINING FAILED: Max CEGAR iterations reached without full verification.")

    logger.info("Training Finished")
    tracker.add_run_losses(model_name, total_actor_losses, total_critic_losses)
    tracker.save_top10_losses_plot(folder=run_dir)
    logger.info(f"Loss plots saved to {run_dir}")


if __name__ == "__main__":
    run_las_lyapunov_ac_cegar_training()
