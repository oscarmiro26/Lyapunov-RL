import numpy as np
import torch
from typing import Callable
from torch import nn
from torch.optim.lr_scheduler import StepLR
import torch.nn.functional as F
import matplotlib.pyplot as plt

from util.sampling import sample_two_headed_gaussian_model, sample_in_region, sample_out_of_region
from util.rk4_step import rk4_step
from trainers.abstract_trainer import Trainer


class LyapunovACTrainer(Trainer):
    def __init__(
        self, 
        actor: nn.Module, 
        critic: nn.Module,
        actor_lr: float, 
        critic_lr: float,
        alpha: float,
        batch_size: int,
        num_paths_sampled: int,
        norm_threshold: float,
        integ_threshold: int,
        dt: float,
        dynamics_fn: Callable,
        state_space: int,
        r1_bounds: list,
        device: str
    ):
        super().__init__()
        self.actor_model = actor
        self.critic_model = critic

        self.alpha = alpha
        self.batch_size = batch_size
        self.num_paths_sampled = num_paths_sampled
        self.norm_threshold = norm_threshold
        self.integ_threshold = integ_threshold
        self.dt = dt

        self.dynamics_fn = dynamics_fn

        self.state_space = state_space
        self.lb, self.ub = r1_bounds

        self.device = device

        self.optimizer = torch.optim.Adam(list(actor.parameters()) + list(critic.parameters()), lr=actor_lr)
        self.scheduler = StepLR(self.optimizer, step_size=500, gamma=0.8)

        # self.actor_optimizer = torch.optim.Adam(actor.parameters(), lr=actor_lr)
        # self.critic_optimizer = torch.optim.Adam(critic.parameters(), lr=critic_lr)

        # self.actor_scheduler = StepLR(self.actor_optimizer, step_size=500, gamma=0.8)
        # self.critic_scheduler = StepLR(self.critic_optimizer, step_size=500, gamma=0.8)

        self.timesteps = 0

        print('Lyapunov Trainer Initialized!')

    def train(self):
        init_states = []
        values = []

        for _ in range(self.num_paths_sampled):
            traj, value, _ = self.simulate_trajectory()
            init_states.append(traj[0])
            values.append(value)

        init_states = torch.stack(init_states)
        values = torch.as_tensor(values, dtype=torch.float32, device=self.device)

        actor_loss = 0
        critic_loss = 0

        # 1) Enforces W(0) = 0
        zeros_tensor = torch.zeros((1, self.state_space), dtype=torch.float32, device=self.device)
        Lz = 5 * torch.square(self.critic_model(zeros_tensor))

        # 2) Enforces W(x) = tanh(alpha * V(x))
        Wx = self.critic_model(init_states)
        target = torch.tanh(self.alpha * values)
        Lr = F.mse_loss(Wx, target)

        # 3) Physics-Informed Loss (PDE residual)
        init_states = sample_in_region(self.batch_size, self.lb, self.ub)  # Sample from R1
        init_states = torch.as_tensor(init_states, dtype=torch.float32, device=self.device)

        Wx, grad_Wx = self.critic_model.forward_with_grad(init_states)
                    
        us = self.actor_model(init_states)
        fxu = self.dynamics_fn(init_states, us)

        phix = torch.linalg.vector_norm(init_states, ord=2, dim=1)
        resid = torch.sum(grad_Wx * fxu.detach(), dim=1) + self.alpha * (1 + Wx) * (1 - Wx) * phix
        Lp = torch.mean(torch.square(resid))

        # 4) Encourage control actions that decrease the Lyapunov function
        grad_norm = torch.linalg.vector_norm(grad_Wx, ord=2, dim=1, keepdim=True)
        unit_grad = grad_Wx / (grad_norm + 1e-8)
        Lc = torch.mean(torch.sum(unit_grad.detach() * fxu, dim=1))

        # 5) Enforce that on the boundary of R2, W(x) ≈ 1
        init_states = sample_out_of_region(self.batch_size, self.lb, self.ub, scale=2)  # Sample from R2
        init_states = torch.as_tensor(init_states, dtype=torch.float32, device=self.device)
        Wx = self.critic_model(init_states)
        Lb = 2 * torch.mean(torch.abs(Wx - 1.0))

        actor_loss = Lc
        critic_loss = Lz + Lr + Lp + Lb

        total_loss = actor_loss + critic_loss

        self.optimizer.zero_grad()
        total_loss.backward()
        self.optimizer.step()

        self.scheduler.step()

        if False:
            # Update the actor
            self.actor_optimizer.zero_grad()
            actor_loss.backward()
            self.actor_optimizer.step()

            # Update the critic
            self.critic_optimizer.zero_grad()
            critic_loss.backward()
            self.critic_optimizer.step()

            # Do a step on the Scheduler
            self.actor_scheduler.step()
            self.critic_scheduler.step()

        self.timesteps += 1
        if self.timesteps % 20 == 0:
            self.plot_level_set_and_trajectories()

        return actor_loss.item(), critic_loss.item()


    @torch.no_grad()
    def simulate_trajectory(self, x=None, max_steps=int(1e7)):
        """
        Simulate a trajectory using an RK4 integrator.
        
        This function approximates the continuous-time trajectory of the system:
            dx/dt = f(x, u)
        where f is computed by the differentiable dynamics function f_torch.
        
        Termination conditions:
        1. The state norm falls below self.norm_threshold.
        2. The trajectory stabilizes (the difference between the current state and the state 10 steps ago is very small).
        3. The integrated state norm exceeds self.integ_threshold.
        4. The number of steps exceeds max_steps.
        
        :param x: (Optional) a starting state tensor of shape [1, nx]. If None, a state is sampled from R1.
        :param max_steps: Maximum number of simulation steps.
        :return:
            traj: Tensor of shape [steps, nx] representing the trajectory.
            integ_acc: A float representing the integrated norm of the state over time.
            convergence: Boolean, True if the trajectory converges (i.e., stops because of low norm or stabilization),
                        False if it stops because the integrated norm threshold is exceeded.
        """
        integ_acc = 0.0
        steps = 0
        
        # If no starting state is provided, sample one from the region R1.
        if x is None:
            x_np = sample_in_region(1, self.lb, self.ub)
            x = torch.as_tensor(x_np, dtype=torch.float32, device=self.device)
        
        traj = [x.clone()]
        
        while True:
            steps += 1
            # Compute the Euclidean norm of the current state.
            norm = torch.linalg.vector_norm(x, ord=2).item()
            integ_acc += norm * self.dt
            
            # Terminate if:
            # (a) the state norm is very low,
            # (b) the trajectory stabilizes (difference between current state and state 10 steps ago is small),
            # (c) maximum number of steps is reached.
            if (norm < self.norm_threshold or 
                (len(traj) > 10 and torch.linalg.vector_norm(traj[-1] - traj[-10], ord=2).item() < 1e-3) or 
                steps >= max_steps):
                return torch.cat(traj, dim=0), integ_acc, True
            
            # Alternatively, if the integrated norm exceeds a threshold, we consider it divergent.
            elif integ_acc > self.integ_threshold:
                return torch.cat(traj, dim=0), integ_acc, False
            
            # Obtain the control input from the actor.
            # Here we sample from the actor's two-headed Gaussian policy.
            u = self.actor_model(x)
            # If the control dimension is 1, ensure u has shape [batch_size, 1].
            if u.dim() == 1:
                u = u.unsqueeze(1)
            
            # Use RK4 integration to compute the next state.
            x_next = rk4_step(self.dynamics_fn, traj[-1], u, dt=self.dt)
            x = x_next.clone()
            traj.append(x.clone())

    def plot_level_set_and_trajectories(self):
        # Create a grid covering R2 (boundary scaled by 2)
        x_min, x_max = self.lb[0]*2, self.ub[0]*2
        y_min, y_max = self.lb[1]*2, self.ub[1]*2
        xx = np.linspace(x_min, x_max, 300)
        yy = np.linspace(y_min, y_max, 300)
        X, Y = np.meshgrid(xx, yy)
        grid_points = np.stack([X.flatten(), Y.flatten()], axis=1)
        grid_tensor = torch.as_tensor(grid_points, dtype=torch.float32, device=self.device)
        
        with torch.no_grad():
            Z = self.critic_model(grid_tensor).cpu().numpy()
        Z = Z.reshape(X.shape)
        
        plt.figure(figsize=(6,5))
        cp = plt.contourf(X, Y, Z, levels=50, cmap='viridis')
        plt.colorbar(cp)
        plt.title("Critic Level Set (Lyapunov Function)")
        plt.xlabel("x[0]")
        plt.ylabel("x[1]")
        
        # Overlay a few trajectories
        for _ in range(5):
            traj, _, converged = self.simulate_trajectory(max_steps=3000)
            if converged:
                traj_np = traj.cpu().numpy()
                plt.plot(traj_np[:, 0], traj_np[:, 1], 'r-', linewidth=1)
                plt.plot(traj_np[0, 0], traj_np[0, 1], 'ro')  # starting point marker
        plt.gca().set_aspect('equal')
        plt.savefig(f"./plots/lyAC_{self.timesteps}.png")
        plt.close()
