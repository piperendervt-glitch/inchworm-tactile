"""Bullet rigid-body contact dynamics. No prescribed locomotion or pose rollback."""
import math
from panda3d.core import Vec3, Point3, NodePath, TransformState, Quat
from panda3d.bullet import (BulletWorld, BulletRigidBodyNode, BulletBoxShape,
    BulletCapsuleShape, XUp, BulletSphereShape, BulletPlaneShape, BulletCylinderShape, BulletGenericConstraint)


class RigidMechanics:
    def __init__(self, settings=None, spawn=None, objects=()):
        self.settings=dict(strategy='rigid_contact',mass_kg=.15,gravity=9.81,
            mu_released=.35,mu_gripped=1.2,joint_torque_nm=.035,
            joint_rate_rad_s=1.8,grip_force_n=2.,substeps=8)
        self.settings.update(settings or {})
        for k in ('mass_kg','gravity','joint_torque_nm','joint_rate_rad_s','grip_force_n'):
            if not math.isfinite(self.settings[k]) or self.settings[k]<=0:raise ValueError('Invalid physics '+k)
        if not isinstance(self.settings['substeps'],int) or not 1<=self.settings['substeps']<=32:raise ValueError('Invalid substeps')
        for k in ('mu_released','mu_gripped'):
            if not math.isfinite(self.settings[k]) or self.settings[k]<0:raise ValueError('Invalid friction')
        self.world=BulletWorld();self.world.setGravity(Vec3(0,0,-self.settings['gravity']))
        self.root=NodePath('physics');self.static=[];self.obstacles=[]
        floor=self.make('floor',0,BulletPlaneShape(Vec3(0,0,1),0),(0,0,0))
        floor.node().setFriction(1.);self.static.append(floor)
        for o in objects:
            if o['kind']!='obstacle':continue  # Food and harm are contact-sensitive material regions.
            shape=BulletCylinderShape(o['radius'],o['height']);shape.setMargin(.001)
            p=self.make('obstacle',0,shape,(o['x'],o['y'],o['height']/2))
            self.static.append(p);self.obstacles.append(p)
        spawn=spawn or dict(x=0.,y=0.,heading_deg=0.)
        q=Quat();q.setFromAxisAngle(spawn['heading_deg'],Vec3(0,0,1))
        origin=Vec3(spawn['x'],spawn['y'],0)
        def pos(x,y,z):return origin+q.xform(Vec3(x,y,z))
        self.segments=[];self.joints=[]
        for i in range(3):
            shape=BulletCapsuleShape(.018,.054,XUp)
            self.segments.append(self.make(('tail','middle','head')[i],self.settings['mass_kg']/3,
                shape,pos(.045+i*.09,0,.019),q))
        self.body=self.segments[1]
        self.supports=[self.segments[0],self.segments[2]]
        for i in range(2):
            joint=BulletGenericConstraint(self.segments[i].node(),self.segments[i+1].node(),
                TransformState.makePos(Vec3(.045,0,0)),TransformState.makePos(Vec3(-.045,0,0)),True)
            for axis in range(3):
                joint.setLinearLimit(axis,0,0)
                joint.setAngularLimit(axis,-52,52)
                motor=joint.getRotationalLimitMotor(axis)
                motor.setMotorEnabled(True);motor.setMaxMotorForce(self.settings['joint_torque_nm'])
            self.world.attachConstraint(joint,True);self.joints.append(joint)
        self.grips=[0.,0.];self.anchors=[None,None]
        self.loads=[0.,0.];self.slip=[0.,0.];self.slip_total=[0.,0.];self.forces=[0.,0.]
        self.capacity=[0.,0.];self.states=['air','air'];self.reason='rigid contact'
        self.obstacle_contact=False

    def make(self,name,mass,shape,pos,quat=None):
        node=BulletRigidBodyNode(name);node.setMass(mass);node.addShape(shape)
        node.setFriction(.6);node.setRestitution(0.)
        node.setLinearDamping(.08);node.setAngularDamping(.15)
        node.setDeactivationEnabled(False)
        path=self.root.attachNewNode(node);path.setPos(pos)
        if quat is not None:path.setQuat(quat)
        self.world.attachRigidBody(node)
        return path

    def point(self,path,xyz):return tuple(path.getPos()+path.getQuat().xform(Vec3(*xyz)))
    def feet(self):return [self.point(p,(0,0,-.018)) for p in self.supports]
    def center(self):return tuple(self.body.getPos())
    def head_position(self):return self.point(self.segments[2],(.045,0,0))
    def heading(self):
        v=self.body.getQuat().xform(Vec3(1,0,0));return math.atan2(v.y,v.x)
    def orientation(self):
        v=self.body.getQuat().xform(Vec3(1,0,0))
        return (-math.atan2(v.z,math.hypot(v.x,v.y)),self.heading(),math.radians(self.body.getR()))
    def angles(self):return [j.getAngle(a) for j in self.joints for a in range(3)]
    def geometry(self):
        return [self.point(self.segments[0],(-.045,0,0))]+[self.point(p,(.045,0,0)) for p in self.segments]
    def surface_points(self):
        return [self.point(p,((row-1)*.015,(col-1)*.012,-math.sqrt(.018**2-((col-1)*.012)**2)))
            for p in self.segments for row in range(3) for col in range(3)]

    def contacts(self,leg):
        return [c for ground in self.static for c in self.world.contactTestPair(leg.node(),ground.node()).getContacts()
                if c.getManifoldPoint().getDistance()<.001]

    def advance(self,targets,dt,grips):
        if len(targets)!=6 or not all(math.isfinite(v) for v in targets):raise ValueError('Six finite angles required')
        if len(grips)!=2 or not all(math.isfinite(g) and 0<=g<=1 for g in grips):raise ValueError('Grips must be in [0,1]')
        self.grips=list(grips);before=self.feet();self.loads=[0.,0.];self.forces=[0.,0.]
        substeps=self.settings['substeps'];h=dt/substeps
        for _ in range(substeps):
            for i,joint in enumerate(self.joints):
                for axis in range(3):
                    error=targets[i*3+axis]-joint.getAngle(axis)
                    rate=self.settings['joint_rate_rad_s']
                    joint.getRotationalLimitMotor(axis).setTargetVelocity(max(-rate,min(rate,error*12)))
            for i,leg in enumerate(self.supports):
                contacts=self.contacts(leg);g=grips[i]
                leg.node().setFriction(self.settings['mu_released']+(self.settings['mu_gripped']-self.settings['mu_released'])*g)
                if g<.1:self.anchors[i]=None
                if self.anchors[i] is None and contacts and g>=.1:
                    c=contacts[0];worldpoint=c.getManifoldPoint().getPositionWorldOnA()
                    local=leg.getQuat().conjugate().xform(worldpoint-leg.getPos())
                    self.anchors[i]=(Vec3(worldpoint),local)
                anchor=self.anchors[i]
                force=Vec3(0)
                if anchor is not None:
                    fixed,local=anchor;r=leg.getQuat().xform(local)
                    delta=fixed-(leg.getPos()+r)
                    velocity=leg.node().getLinearVelocity()+leg.node().getAngularVelocity().cross(r)
                    force=delta*140-velocity*.8
                    limit=self.settings['grip_force_n']*g
                    # A finite spring grip breaks under overload; no remote air anchoring.
                    if delta.length()>.012 or force.length()>limit*2:
                        self.anchors[i]=None;force=Vec3(0)
                    else:
                        if force.length()>limit:force*=limit/force.length()
                        leg.node().applyForce(force,r)
                self.forces[i]+=force.length()/substeps
            self.world.doPhysics(h,0)
            for i,leg in enumerate(self.supports):
                # Solver impulse / dt gives average normal contact load.
                self.loads[i]+=sum(max(0.,p.getAppliedImpulse()) for m in self.world.getManifolds()
                    if (m.getNode0()==leg.node() and any(m.getNode1()==g.node() for g in self.static))
                    or (m.getNode1()==leg.node() and any(m.getNode0()==g.node() for g in self.static))
                    for p in m.getManifoldPoints() if p.getDistance()<.001)/dt
        after=self.feet()
        self.slip=[math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(before,after)]
        self.slip_total=[a+b for a,b in zip(self.slip_total,self.slip)]
        self.states=['grip' if self.anchors[i] is not None else ('contact' if self.contacts(p) else 'air') for i,p in enumerate(self.supports)]
        self.capacity=[self.settings['grip_force_n']*g for g in grips]
        self.obstacle_contact=any(self.world.contactTestPair(p.node(),o.node()).getNumContacts()>0 for p in [self.body]+self.supports for o in self.obstacles)
        self.reason='obstacle contact' if self.obstacle_contact else 'rigid contact'
