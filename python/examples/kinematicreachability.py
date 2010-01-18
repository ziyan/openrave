#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import with_statement # for python 2.5
__author__ = 'Rosen Diankov'
__copyright__ = 'Copyright (C) 2009-2010 Rosen Diankov (rosen.diankov@gmail.com)'
__license__ = 'Apache License, Version 2.0'

import openravepy
from openravepy import *
from openravepy.examples import inversekinematics
from numpy import *
import time
import heapq # for nth smallest element
from optparse import OptionParser

class ReachabilityModel(OpenRAVEModel):
    """Computes the robot manipulator's reachability space (stores it in 6D) and
    offers several functions to use it effectively in planning."""
    def __init__(self,robot):
        OpenRAVEModel.__init__(self,robot=robot)
        self.ikmodel = inversekinematics.InverseKinematicsModel(robot=robot)
        if not self.ikmodel.load():
            self.ikmodel.autogenerate()
        self.trimesh = self.env.Triangulate(self.robot)
        self.reachabilitystats = None
        self.reachabilitydensity3d = None
        self.pointscale = None
        self.xyzdelta = None
        self.quatdelta = None

    def has(self):
        return len(self.reachabilitydensity3d) > 0

    def load(self):
        params = OpenRAVEModel.load(self)
        if params is None:
            return False
        self.reachabilitystats,self.reachabilitydensity3d,self.pointscale,self.xyzdelta,self.quatdelta = params
        return self.has()

    def save(self):
        OpenRAVEModel.save(self,(self.reachabilitystats,self.reachabilitydensity3d,self.pointscale,self.xyzdelta,self.quatdelta))

    def getfilename(self):
        return os.path.join(OpenRAVEModel.getfilename(self),'reachability.' + self.manip.GetName() + '.pp')

    def generateFromOptions(self,options):
        self.generate(maxradius=options.maxradius,xyzdelta=options.xyzdelta,quatdelta=options.quatdelta)

    def generate(self,maxradius=None,translationonly=False,xyzdelta=0.04,quatdelta=0.25):
        starttime = time.time()
        # the axes' anchors are the best way to find th emax radius
        eeanchor = self.robot.GetJoints()[self.manip.GetArmJoints()[-1]].GetAnchor()
        eetrans = self.manip.GetEndEffectorTransform()[0:3,3]
        baseanchor = self.robot.GetJoints()[self.manip.GetArmJoints()[0]].GetAnchor()
        armlength = sqrt(sum((eetrans-baseanchor)**2))
        if maxradius is None:
            maxradius = armlength+0.05
        print 'radius: %f'%maxradius

        allpoints,insideinds,shape,self.pointscale = self.UniformlySampleSpace(maxradius,delta=xyzdelta)
        # select the best sphere level matching quatdelta;
        # level=0, quatdist = 0.5160220
        # level=1: quatdist = 0.2523583
        # level=2: quatdist = 0.120735
        qarray = SpaceSampler().sampleSO3(level=max(0,int(0.5-log2(0.516))))
        rotations = [eye(3)] if translationonly else rotationMatrixFromQArray(qarray)
        self.xyzdelta = xyzdelta
        self.quatdelta = 0
        if not translationonly:
            # for rotations, get the average distance to the nearest rotation
            neighdists = []
            for q in qarray:
                neighdists.append(heapq.nsmallest(2,quatArrayTDist(q,qarray))[1])
            self.quatdelta = mean(neighdists)
        
        T = eye(4)
        reachabilitydensity3d = zeros(prod(shape))
        self.reachabilitystats = []
        with self.env:
            for i,ind in enumerate(insideinds):
                numvalid = 0
                T[0:3,3] = allpoints[ind]
                for rotation in rotations:
                    T[0:3,0:3] = rotation
                    solutions = self.manip.FindIKSolutions(T,True)
                    if solutions is not None:
                        self.reachabilitystats.append(r_[poseFromMatrix(T),len(solutions)])
                        numvalid += len(solutions)
                if mod(i,1000)==0:
                    print '%d/%d'%(i,len(insideinds))
                reachabilitydensity3d[ind] = numvalid/float(len(rotations))
        self.reachabilitydensity3d = reshape(reachabilitydensity3d/50.0,shape)
        self.reachabilitystats = array(self.reachabilitystats)
        print 'reachability finished in %fs'%(time.time()-starttime)

    def show(self,showrobot=True,contours=[0.1,0.5,0.9,0.99],opacity=None,figureid=1, xrange=None):
        mlab.figure(figureid,fgcolor=(0,0,0), bgcolor=(1,1,1),size=(1024,768))
        mlab.clf()
        reachabilitydensity3d = minimum(self.reachabilitydensity3d,1.0)
        reachabilitydensity3d[0,0,0] = 1 # have at least one point be at the maximum
        if xrange is None:
            offset = array((0,0,0))
            src = mlab.pipeline.scalar_field(reachabilitydensity3d)
        else:
            offset = array((xrange[0]-1,0,0))
            src = mlab.pipeline.scalar_field(r_[zeros((1,)+reachabilitydensity3d.shape[1:]),reachabilitydensity3d[xrange,:,:],zeros((1,)+reachabilitydensity3d.shape[1:])])
            
        for i,c in enumerate(contours):
            mlab.pipeline.iso_surface(src,contours=[c],opacity=min(1,0.7*c if opacity is None else opacity[i]))
        #mlab.pipeline.volume(mlab.pipeline.scalar_field(reachabilitydensity3d*100))
        if showrobot:
            v = self.pointscale[0]*self.trimesh.vertices+self.pointscale[1]
            mlab.triangular_mesh(v[:,0]-offset[0],v[:,1]-offset[1],v[:,2]-offset[2],self.trimesh.indices,color=(0.5,0.5,0.5))
        mlab.show()

    def autogenerate(self,forcegenerate=True):
        # disable every body but the target and robot
        bodies = [b for b in self.env.GetBodies() if b.GetNetworkId() != self.robot.GetNetworkId()]
        for b in bodies:
            b.Enable(False)
        try:
            if self.robot.GetRobotStructureHash() == '409764e862c254605cafb9de013eb531' and self.manip.GetName() == 'arm':
                self.generate(maxradius=1.1)
            else:
                if not forcegenerate:
                    raise ValueError('failed to find auto-generation parameters')
                self.generate()
                raise ValueError('could not auto-generate reachability for %s:%s'%(self.robot.GetName(),self.manip.GetName()))
            self.save()
        finally:
            for b in bodies:
                b.Enable(True)

    def UniformlySampleSpace(self,maxradius,delta):
        nsteps = floor(maxradius/delta)
        X,Y,Z = mgrid[-nsteps:nsteps,-nsteps:nsteps,-nsteps:nsteps]
        allpoints = c_[X.flat,Y.flat,Z.flat]*delta
        insideinds = flatnonzero(sum(allpoints**2,1)<maxradius**2)
        return allpoints,insideinds,X.shape,array((1.0/delta,nsteps))

    @staticmethod
    def CreateOptionParser():
        parser = OpenRAVEModel.CreateOptionParser()
        parser.description='Computes the reachability region of a robot manipulator and python pickles it into a file.'
        parser.add_option('--maxradius',action='store',type='float',dest='maxradius',default=None,
                          help='The max radius of the arm to perform the computation')
        parser.add_option('--xyzdelta',action='store',type='float',dest='xyzdelta',default=0.04,
                          help='The max radius of the arm to perform the computation')
        parser.add_option('--quatdelta',action='store',type='float',dest='quatdelta',default=0.25,
                          help='The max radius of the arm to perform the computation')
        return parser
    @staticmethod
    def RunFromParser(Model=None,parser=None):
        if parser is None:
            parser = ReachabilityModel.CreateOptionParser()
        (options, args) = parser.parse_args()
        env = Environment()
        try:
            if Model is None:
                Model = lambda robot: ReachabilityModel(robot=robot)
            OpenRAVEModel.RunFromParser(env=env,Model=Model,parser=parser)
        finally:
            env.Destroy()

if __name__=='__main__':
    try:
        from enthought.mayavi import mlab
    except ImportError:
        pass
    ReachabilityModel.RunFromParser()
