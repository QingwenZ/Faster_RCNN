"""
Faster R-CNN
Training.

Copyright (c) 2019 Haohang Huang
Licensed under the MIT License (see LICENSE for details)
Written by Haohang Huang, November 2019.
"""

import torch
import torch.nn as nn
import torch.optim as optim
import torchvision
import torchvision.transforms as transforms

from faster_rcnn import FasterRCNN
from config import Config as cfg
from dataset import COCODataset, PascalDataset
import rpn.utils

device = cfg.DEVICE
num_epochs = cfg.NUM_EPOCHS
batch_size = cfg.BATCH_SIZE
learning_rate = cfg.LEARNING_RATE

#trainloader, testloader = get_dataloader(dataset='COCO')
model = FasterRCNN().to(device)
optimizer = optim.Adam(model.parameters(), lr=learning_rate)
scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='max', patience=3, verbose=True) # dynamic LR scheduler

def main():
    # f = open('D_acc1.csv', 'w+')

    # Training
    for epoch in range(num_epochs):
        print("Epoch {}...".format(epoch + 1), end='', flush=True)

        images = torch.abs(torch.randn(batch_size*3*600*800).view(batch_size,3,600,800))
        gt_boxes = torch.ones(batch_size,20,4).float() # remember to make gt_boxes as float!
        gt_boxes[:,:,2] = 50
        gt_boxes[:,:,3] = 50
        gt_classes = torch.randint(0,20, (batch_size,20))

        images, gt_boxes, gt_classes = images.to(device), gt_boxes.to(device), gt_classes.to(device)

        # Train
        print("Training...", end='', flush=True)
        model.train()
        train_loss, total, correct = 0, 0, 0
        #for batch_idx, (images, gt_boxes) in enumerate(trainloader):
            #images, gt_boxes = images.to(device), gt_boxes.to(device)
        for step in range(10):

            optimizer.zero_grad() # reset gradient
            rois, pred_rois_classes, pred_rois_coeffs, rcnn_loss = model(images, gt_boxes, gt_classes) # forward step

            #if evaluate:
                # 7. transform RoIs to final results
                # Note: I haven't apply the empirical mean/std for coeff yet, do it later here before applying the transform
            #    rois = rpn.utils.bbox_transform(rois, pred_rois_coeffs)

            rcnn_loss.backward() # calculate gradients
            print("rcnn_loss:", rcnn_loss.data)
            # train_loss += loss
            # _, predicted = torch.max(results.data, 1)
            # total += labels.size(0)
            # correct += (predicted == labels).sum().item()

            optimizer.step() # update parameters

        # Test
        # print("Evaluating...")
        # model.eval()
        # train_loss /= len(trainloader) # average among batches
        # train_acc = correct/total * 100
        # test_loss, test_acc = evaluation(testloader)
        # print("training loss={:.2f}, training accuracy={:.1f}, test loss={:.2f}, test accuracy={:.1f}".format(train_loss, train_acc, test_loss, test_acc))
        #
        # # write to csv
        # print("{:d},{:.1f},{:.1f}".format(epoch+1, train_acc, test_acc), file=f, flush=True)

    # Save
    # f.close()
    # torch.save(model,'rcnn.model')

def evaluation(dataloader):
    with torch.no_grad():
        loss, accuracy, total, correct = 0, 0, 0, 0
        for images, labels in dataloader:
            images, labels = images.to(device), labels.to(device)

            _, results = model(images)
            loss += loss_cross_entropy(results, labels)

            _, predicted = torch.max(results.data, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()

        loss /= len(dataloader) # average among batches
        accuracy = correct/total * 100

    return loss, accuracy

def get_dataloader(dataset='COCO'):
    """Return dataloader for training set and test set. (tentative)
    """
    # Load CIFAR10 dataset: 32x32 color images with 10 classes
    transform_train = transforms.Compose([
        transforms.RandomResizedCrop(32, scale=(0.7, 1.0), ratio=(1.0,1.0)),
        transforms.ColorJitter(
                brightness=0.1*abs(torch.randn(1).item()),
                contrast=0.1*abs(torch.randn(1).item()),
                saturation=0.1*abs(torch.randn(1).item()),
                hue=0.1*abs(torch.randn(1).item())),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
    ])

    transform_test = transforms.Compose([
        transforms.CenterCrop(32),
        transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
    ])

    trainset = torchvision.datasets.CIFAR10(root='./', train=True, download=False, transform=transform_train)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=batch_size, shuffle=True, num_workers=8)

    testset = torchvision.datasets.CIFAR10(root='./', train=False, download=False, transform=transform_test)
    testloader = torch.utils.data.DataLoader(testset, batch_size=batch_size, shuffle=False, num_workers=8)

    return trainloader, testloader

if __name__ == '__main__':
    main()
