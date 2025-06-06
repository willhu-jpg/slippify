# Add the current project's src directory to the front of the path
import sys
import os

# Get the absolute path to the src directory
current_dir = os.path.dirname(os.path.abspath(__file__))  # scripts directory
src_dir = os.path.dirname(current_dir)  # src directory
sys.path.insert(0, src_dir)

import pydra
from pydra import REQUIRED, Config

# Now import the modules
from infra.biggerModel import BigEncoder
from infra.model import Encoder
from data.replay_buffer import ReplayBuffer
from torch.utils.data import DataLoader
import torch
from tqdm import tqdm
import wandb
import torch.nn.functional as F

class TrainEncoderConfig(Config):
    def __init__(self):
        self.name = "train_encoder"
        self.model = "bigger"
        self.transforms = ["default", "jitter"]
        self.dropout = 0.1
        self.learning_rate = 1e-3
        self.batch_size = 128
        self.epochs = 30
        self.z_dim = 10

    def __repr__(self):
        return f"TrainEncoderConfig({self.to_dict()})"
    
def load_datasets(transforms):
    train_dataset = ReplayBuffer(
        root_dir=f"/home/ubuntu/project/slippify/data_split/train/",
        transforms=transforms,
    )
    val_dataset = ReplayBuffer(
        root_dir=f"/home/ubuntu/project/slippify/data_split/val/",
        transforms=["default"],
    )
    test_dataset = ReplayBuffer(
        root_dir=f"/home/ubuntu/project/slippify/data_split/test/",
        transforms=["default"],
    )
    return train_dataset, val_dataset, test_dataset

def normalize_datasets_attr(datasets, attr):
    attr_tensor = getattr(datasets[0], attr)
    for i in range(1, len(datasets)):
        attr_tensor = torch.concat((attr_tensor, getattr(datasets[i], attr)), dim=0)

    mean = torch.mean(attr_tensor, dim=0)
    std = torch.std(attr_tensor, dim=0)
    print(f"Data mean: {mean}, Data std: {std}")
    
    for dataset in datasets:
        setattr(dataset, attr, (getattr(dataset, attr) - mean) / (std + 1e-8))

@pydra.main(base=TrainEncoderConfig)
def main(config: TrainEncoderConfig):

    # print the config
    print(config)

    # Initialize wandb
    wandb.init(
        project="slippi-frame-autoencoder",
        name=config.name,
        config={
            "learning_rate": config.learning_rate,
            "batch_size": config.batch_size,
            "epochs": config.epochs,
            "z_dim": config.z_dim,
            "dropout": config.dropout,
            "architecture": config.model,
            "dataset": "slippi_frames",
            "image_size": 64,
        }
    )
    
    # Initialize model and optimizer
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if config.model == "bigger":
        model = BigEncoder(z_dim=config.z_dim, dropout=config.dropout).to(device)
    else:
        model = Encoder(z_dim=config.z_dim, dropout=config.dropout).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=wandb.config.learning_rate)

    # Initialize train, validation, and test datasets
    datasets = load_datasets(config.transforms)
    normalize_datasets_attr(datasets, "observations")
    train_dataset, val_dataset, test_dataset = datasets

    train_dataloader = DataLoader(train_dataset, batch_size=wandb.config.batch_size, shuffle=True)
    val_dataloader = DataLoader(val_dataset, batch_size=wandb.config.batch_size, shuffle=True)
    test_dataloader = DataLoader(test_dataset, batch_size=wandb.config.batch_size, shuffle=True)

    # Log model architecture
    wandb.watch(model, log="all")

    for epoch in tqdm(range(wandb.config.epochs), desc="Epochs"):
        model.train()

        for batch in tqdm(train_dataloader, desc=f"Epoch {epoch}", leave=False):
            (observations, actions, next_observations), frames = batch

            frames = frames.squeeze(0).to(device)
            observations = observations.squeeze(1).to(device)

            y = model(frames)

            loss = F.mse_loss(y, observations, reduction="mean")

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        wandb.log({
            "epoch": epoch,
            "loss": loss.item(),
        })

        print(f"Epoch {epoch}, Loss: {loss.item()}")

         # every few epochs, compute validation loss:
        if epoch % 5 == 0:
            model.eval()
            with torch.no_grad():
                val_loss = 0
                for batch in tqdm(val_dataloader, desc=f"Epoch {epoch}", leave=False):
                    (observations, actions, next_observations), frames = batch

                    frames = frames.squeeze(0).to(device)
                    observations = observations.squeeze(1).to(device)

                    y = model(frames)
                    val_loss += F.mse_loss(y, observations, reduction="mean").item()
                    
                val_loss /= len(val_dataloader)
                wandb.log({
                    "val_loss": val_loss,
                })
                print(f"Validation Loss: {val_loss}")

    # compute test loss
    model.eval()
    with torch.no_grad():
        test_loss = 0
        for batch in tqdm(test_dataloader, desc=f"Epoch {epoch}", leave=False):
            (observations, actions, next_observations), frames = batch
            frames = frames.squeeze(0).to(device)
            observations = observations.squeeze(1).to(device)

            y = model(frames)
            test_loss += F.mse_loss(y, observations, reduction="mean").item()

        test_loss /= len(test_dataloader)
        wandb.log({
            "test_loss": test_loss,
        })
        print(f"Test Loss: {test_loss}")

    torch.save(model.state_dict(), f"{config.name}.pth")
    
    # Save model as wandb artifact
    artifact = wandb.Artifact("model", type="model")
    artifact.add_file(f"{config.name}.pth")
    wandb.log_artifact(artifact)
    
    print(f"Model saved to {config.name}.pth")
    wandb.finish()

if __name__ == "__main__":
    main()