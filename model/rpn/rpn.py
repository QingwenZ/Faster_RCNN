"""
Faster R-CNN
Region Proposal Network.

Copyright (c) 2019 Haohang Huang
Licensed under the MIT License (see LICENSE for details)
Written by Haohang Huang, November 2019.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

from config import Config as cfg
from .anchor_generation import AnchorGeneration
from .proposal import Proposal
from .anchor_refine import AnchorRefine
from .proposal_refine import ProposalRefine

class Flatten(nn.Module):
    """Customized flatten operation other than nn.Flatten()
    """
    def __init__(self, last_dim):
        super(Flatten, self).__init__()
        self.last_dim = last_dim

    def forward(self, x):
        """Input x is N x (9*2) x H/16 x W/16, we want to reshape to N x (H/16*W/16*9) x 2. N is minibatch size, self.last_dim can be 2 or 4 in our context.
        """
        return x.permute(0,2,3,1).contiguous().view(x.size(0), -1, self.last_dim)

class RPN(nn.Module):
    """Regional Proposal Network.
    """
    def __init__(self):
        super(RPN, self).__init__()

        # generate anchors by sliding-window
        self.anchors = AnchorGeneration(img_size=cfg.IMG_SIZE,
                                        stride=cfg.RPN_ANCHOR_STRIDE,
                                        scales=cfg.RPN_ANCHOR_SCALES,
                                        ratios=cfg.RPN_ANCHOR_RATIOS
                                        ).generate_all().to(cfg.DEVICE) # move to device

        # normal conv layer to reduce channel from 1024-->512
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels=cfg.RES_OUT_CHANNEL,
                      out_channels=cfg.CONV_OUT_CHANNEL,
                      kernel_size=3, stride=1, padding=1),
            nn.ReLU(inplace=True)
        )

        """Here are two important layers.
        Feature Map --> bbox foreground/background score
                    --> bbox transformation coefficients
        We have:
            1. feature maps: N x 512 x H/16 x W/16
            2. anchors: H/16 x W/16 x 9 x 4, (H/16*W/16) is No. of anchor locations, 9 is anchor types per anchor location, 4 is box dimension
        We want to learn from the feature maps some useful information about the generated anchors to help us SELECT "good" anchors among them, i.e. we want to generate the following two metrics from feature maps:
            1. H/16 x W/16 x 9 x 2 where 2 is (fg, bg) foreground/background probability
            -- classification scores
            2. H/16 x W/16 x 9 x 4 where 4 is coefficient to deform the anchors to fit the object better
            -- bounding box regression coefficients
        The way is to use conv1x1 to preserve the H/16 x W/16 dimension, and set output channel to 9x2 and 9x4, followed by reshape/view operation into bbox_score and bbox_coeff.
        """
        anchor_types = len(cfg.RPN_ANCHOR_RATIOS) * len(cfg.RPN_ANCHOR_SCALES)
        # conv layer1 to predict foreground/background probability per anchor
        self.conv_bbox_score = nn.Sequential(
            nn.Conv2d(cfg.CONV_OUT_CHANNEL, anchor_types * 2, 1, 1, 0),
            Flatten(2),
            #nn.Softmax(dim=2)
        )

        # conv layer2 to predict bbox regression coefficients per anchor
        self.conv_bbox_coeff = nn.Sequential(
            nn.Conv2d(cfg.CONV_OUT_CHANNEL, anchor_types * 4, 1, 1, 0),
            Flatten(4)
        )

        """Select/Propose "good" anchors from all generated anchors based on the bbox_socre & bbox_coeff from previous conv layers.
        bbox foreground/background score
                        +         --> "good" anchors' score
        bbox transformation coefficients
                        +         --> "good" anchors' RoI
        generated anchors/bboxes
        """
        self.proposal = Proposal()

        # further select "good" anchors for training RPN
        self.anchor_refine = AnchorRefine()

        # further select "good" RoIs for later training the classification
        self.proposal_refine = ProposalRefine()

    def forward(self, feature_map, gt_boxes, gt_classes):
        """Forward step.
        Args:
            feature_map [N x C x H x W]: feature maps after ResNet
            gt_boxes [N x X x 4]: ground-truth boxes in each image sample. Only used in AnchorRefine & ProposalRefine layers.
            gt_classes [N x X]: classes of ground-truth boxes in each image sample. Only used in ProposalRefine layer.
        Returns:
            rois [N x R x 4]: R selected RoIs proposed by Proposal layer (inference) or Proposal + ProposalRefine layers (training)
            rois_labels [N x R]: class labels for RoIs, 0 for background. None if inference
            rois_coeffs [N x R x 4]: target coefficients for RoIs, 0 for bg. None if inference
            *_loss [float]: loss values
        """
        # 1. common conv
        out = self.conv(feature_map)

        # 2. special conv to generate scores and transformation coefficients for all generated anchors
        # define No. of anchors = H/16*W/16*9 as A
        bbox_score = self.conv_bbox_score(out) # N x A x 2
        bbox_coeff = self.conv_bbox_coeff(out) # N x A x 4

        # 3. proposal layer for generating RoIs
        roi_scores, rois = self.proposal(self.anchors, bbox_score, bbox_coeff)
        # roi_scores: N x M x 1 (never used after)
        # rois: N x M x 4

        # 4. anchor refinement layer to select some anchors for training the RPN proposal ability
        # if training: calculate RPN proposal loss (focus on a good proposal)
        rpn_loss, rpn_class_loss, rpn_bbox_loss = 0, 0, 0
        if self.training:
            # 1. classify anchors (fg/bg/dont-care) and calculate target regression coeffs. since bbox_drop is applied, track the kept anchor indices as well
            labels, target_coeffs, anchors_idx = self.anchor_refine(self.anchors, gt_boxes)
            # labels: N x X, with 1/0/-1
            # target_coeffs: N x X x 4
            # anchors_idx: X x 1

            # 2. mask out scores and coeffs of anchors after bbox_drop
            bbox_score_anchors = torch.index_select(bbox_score, dim=1, index=anchors_idx) # N x X x 2
            bbox_coeff_anchors = torch.index_select(bbox_coeff, dim=1, index=anchors_idx) # N x X x 4


            for n in range(labels.size(0)): # can't align among batch, loop
                # 3. calculate classification loss (cross entropy)
                # mask out foreground + background scores
                fg_and_bg = labels[n,:] >= 0 # denote fg+bg = Y
                pred_scores = bbox_score_anchors[n,fg_and_bg,:] # Y x 2
                true_scores = (1 - labels[n,fg_and_bg]).long() # Y
                # a little tricky here:
                # 1. I used col0 as fg and col1 as bg in my bbox_score result, but the label is fg(1) bg(0), so here I should flip the true labels to make the one-hot locations right
                # 2. F.cross_entropy() takes multi-dimensional input as N x C and true label N, where C should be the No. of classes and X is No. of samples.
                rpn_class_loss += F.cross_entropy(pred_scores, true_scores) # by default averaged

                # 4. calculate regression loss (smoothL1)
                # mask out foreground coeffs only
                fg = labels[n,:] == 1 # denote fg = Z
                pred_coeffs = bbox_coeff_anchors[n,fg,:] # Z x 4
                true_coeffs = target_coeffs[n,fg,:] # Z x 4
                rpn_bbox_loss += F.smooth_l1_loss(pred_coeffs, true_coeffs)

            rpn_class_loss /= gt_boxes.size(0) # average minibatch
            rpn_bbox_loss /= gt_boxes.size(0)
            rpn_loss = rpn_class_loss + rpn_bbox_loss

        # 5. proposal refinement layer to select some RoIs for training the RPN classification ability
        # if training: calculate RPN classification loss (focus on a per-class good proposal)
        rois_labels, rois_coeffs = None, None
        if self.training:
            rois, rois_labels, rois_coeffs = self.proposal_refine(rois, gt_boxes, gt_classes)
            # rois: N x R x 4, selected RoIs. Overwrite original rois while training, this "rois" will be passed to classification layer
            # rois_labels: N x R, R is cfg.RPN_TOTAL_ROIS
            # rois_coeffs: N x R x 21*4

        return rois, rois_labels, rois_coeffs, rpn_class_loss, rpn_bbox_loss, rpn_loss

if __name__ == '__main__':
    print(">>> Testing")
    test = RPN()
