import torch
from torch import nn
import torchvision as tv
from torchmetrics import Accuracy
import pandas as pd
from typing import Literal
from pathlib import Path
from tqdm import tqdm

class ClassificationModelWrapper:

    '''
    ModelWrapper class, made for fine-tuning and training classification models

    Attributes
    =====================
        BATCH_SIZE
        LR
        WEIGHT_DECAY
        NAME
        PATH
        device
        patience_curr (internal)
        weights (pre-trained)
        model
        train_dataset
        test_dataset
        num_classes
        train_loader
        test_loader
        criterion
        acc_fn
        optimizer
        PATIENCE
        history

    Methods
    =====================
        __init__(BATCH_SIZE, LR, WEIGHT_DECAY, NAME, PATH)
            initializing basic hyperparameters

        load_model(model: Literal['efficientnet_b0', 'alexnet', 'vgg11', 'vgg11_bn']='efficientnet_b0')
            load pre-trained model from torchvision.models for fine-tuning

        prepare_dataloaders(train_dataset, test_dataset)
            prepare DataLoaders for training.

        init_optim(optimizer: Literal['SGD', 'Adam']='SGD')
            initialize optimizer, criterion and accuracy metric function

        train(EPOCHS, TEST_EVERY, PATIENCE)
            train the model. PATIENCE - early stopping parameter
    '''

    def __init__(self, NAME: str=None, PATH: str|Path=None, LR: float=1e-3, WEIGHT_DECAY: float=1e-4):
        '''
        Initialize necessary hyperparameters

        NAME - the name of your experiment (if None, it generates automatically)
        PATH - where to put the checkpoints and history.csv file of this experiment
        LR - the learning rate
        WEIGHT_DECAY - L2 regularization parameter
        '''

        self.LR = LR
        self.WEIGHT_DECAY = WEIGHT_DECAY
        self.NAME = NAME
        self.PATH = Path(PATH)
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

        self.PATH.mkdir(parents=True, exist_ok=True)


    def load_model(self, num_classes: int,
                   model: Literal['efficientnet_b0', 'alexnet', 'vgg11', 'vgg11_bn']='efficientnet_b0',
                   checkpoint: Path|str=None):
        '''
        Load a pre-trained model from torchvision.models. If you want to build your own architecture \
        - you can just set your model to self.model attribute.

        num_classes - the number of classes in your dataset
        model - the model architecture to load form the server. Possible options: efficientnet_b0, alexnet, vgg11, vgg11_bn.
        checkpoint - path-like object to the .pth checkpoint.
        '''
        
        if model == 'efficientnet_b0':
            self.weights = tv.models.EfficientNet_B0_Weights.DEFAULT
            self.model = tv.models.efficientnet_b0(weights=self.weights)
        elif model == 'alexnet':
            self.weights = tv.models.AlexNet_Weights.DEFAULT
            self.model = tv.models.alexnet(weights=self.weights)
        elif model =='vgg11':
            self.weights = tv.models.VGG11_Weights.DEFAULT
            self.model = tv.models.vgg11(weights=self.weights)
        elif model =='vgg11_bn':
            self.weights = tv.models.VGG11_BN_Weights.DEFAULT
            self.model = tv.models.vgg11_bn(weights=self.weights)
        else:
            raise RuntimeError('Incorrect `model` parameter. Check type hint.')

        # did't test if it works with models except efficientnet
        for parameter in self.model.features.parameters():  
            parameter.requires_grad = False

        self.model.classifier = nn.Sequential(
            nn.Dropout(p=0.2),
            nn.Linear(1280, num_classes)
        )

        self.model = self.model.to(self.device)

        if checkpoint is not None:
            self.model.load_state_dict(torch.load(checkpoint, map_location=self.device))


    def prepare_dataloaders(self,
                            train_dataset: torch.utils.data.Dataset,
                            test_dataset: torch.utils.data.Dataset,
                            BATCH_SIZE: int=32):
        '''
        Prepare the dataloaders for training

        train_dataset - train dataset
        test_dataset - test dataset
        BATCH_SIZE - the batch size you want to use
        '''

        self.train_dataset = train_dataset
        self.test_dataset = test_dataset
        self.BATCH_SIZE = BATCH_SIZE

        self.num_classes = len(self.train_dataset.classes)
        self.train_loader = torch.utils.data.DataLoader(self.train_dataset, batch_size=self.BATCH_SIZE)
        self.test_loader = torch.utils.data.DataLoader(self.test_dataset, batch_size=self.BATCH_SIZE)


    def init_optim(self, optimizer: Literal['SGD', 'Adam']='SGD'):
        '''
        Initialize optimizer, accuracy function and criterion

        optimizer - the optimizer to use. Options: SGD and Adam.
        '''
        
        self.criterion = nn.CrossEntropyLoss() if self.num_classes > 2 else nn.BCEWithLogitsLoss()
        self.acc_fn = Accuracy('multiclass' if self.num_classes > 2 else 'binary', num_classes=self.num_classes).to(self.device)
        if optimizer == 'Adam':
            self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.LR, weight_decay=self.WEIGHT_DECAY)
        elif optimizer == 'SGD':
            self.optimizer = torch.optim.SGD(self.model.parameters(), lr=self.LR, weight_decay=self.WEIGHT_DECAY)
        else:
            raise RuntimeError('Incorrect `optimizer` parameter. Check type hint.')


    def _train_step(self):
        '''
        Perform a train step (internal method)
        '''

        train_loss, train_acc = 0, 0

        self.model.train()
        for train_X, train_y in self.train_loader:
            train_X = train_X.to(self.device)
            train_y = train_y.to(self.device)

            logits = self.model(train_X)
            preds = torch.softmax(logits, dim=1).argmax(dim=1)

            train_loss_batch = self.criterion(logits, train_y)
            train_loss += train_loss_batch.item()
            train_acc_batch = self.acc_fn(preds, train_y)
            train_acc += train_acc_batch.item()

            self.optimizer.zero_grad()
            train_loss_batch.backward()
            self.optimizer.step()

            torch.cuda.empty_cache()
            train_X = train_X.detach().cpu()
            train_y = train_y.detach().cpu()

        train_loss /= len(self.train_loader)
        train_acc /= len(self.train_loader)

        return train_loss, train_acc
    

    def _test_step(self, test_loss_previous):
        '''
        Perform a test step (internal method)
        '''

        test_loss, test_acc = 0, 0

        with torch.inference_mode():
            self.model.eval()
            for test_X, test_y in self.test_loader:
                test_X = test_X.to(self.device)
                test_y = test_y.to(self.device)

                logits = self.model(test_X)
                preds = torch.softmax(logits, dim=1).argmax(dim=1)

                test_loss_batch = self.criterion(logits, test_y)
                test_loss += test_loss_batch.item()
                test_acc_batch = self.acc_fn(preds, test_y)
                test_acc += test_acc_batch.item()

                torch.cuda.empty_cache()
                test_X = test_X.detach().cpu()
                test_y = test_y.detach().cpu()

        test_loss = test_loss / len(self.test_loader)
        test_acc = test_acc / len(self.test_loader)

        if self.PATIENCE is not None:
            if (test_loss_previous < test_loss) and (self.epoch != 0):
                self.patience_curr += 1
                print(f'Test loss increased | {test_loss_previous} => {test_loss} | {self.patience_curr}/{self.PATIENCE}')
                if self.patience_curr == self.PATIENCE:
                    self.patience_ended = True

            elif test_loss_previous > test_loss:
                self.patience_curr = 0
                torch.save(self.model.state_dict(), self.PATH / f'{self.epoch}_{test_loss}_{test_acc}.pth')

        return test_loss, test_acc


    def train(self, EPOCHS, TEST_EVERY, PATIENCE=None):
        '''
        Train a model

        EPOCHS - number of epochs to train
        TEST_EVERY - per how many epochs the model should be tested (and results should be printed)
        PATIENCE - early stopping parameter. if test_loss have been increasing for {patience} epochs, the model will stop training
        '''

        results = []

        self.PATIENCE = PATIENCE
        self.patience_curr = 0
        self.patience_ended = False
        test_loss = 0

        for epoch in tqdm(range(EPOCHS)):
            print('')
            self.epoch = epoch
            if self.patience_ended:
                print(f'Early stopping...')
                break

            train_loss, train_acc = self._train_step()

            if self.epoch % TEST_EVERY == 0:
                test_loss_previous = test_loss
                test_loss, test_acc = self._test_step(test_loss_previous)

                print(f'Epoch {epoch}, train loss {train_loss:.5f}, train accuracy {train_acc:.5f}, test loss {test_loss:.5f}, test accuracy {test_acc:.5f}')
                results.append([epoch, train_loss, train_acc, test_loss, test_acc])
            print('\n')

        self.history = pd.DataFrame(results, columns=['epoch', 'train_loss', 'train_acc', 'test_loss', 'test_acc'])
        return self.history
