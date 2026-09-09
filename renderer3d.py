"""Observer-only GPU 3D renderer. No control or physics updates occur here."""
import math
from PIL import Image
from panda3d.core import (loadPrcFileData, GeomVertexData, GeomVertexFormat,
    GeomVertexWriter, Geom, GeomTriangles, GeomNode, NodePath, AmbientLight,
    DirectionalLight, Vec3, Point3, Texture, GraphicsOutput, LineSegs, TextNode)


def mesh(name, faces):
    data=GeomVertexData(name,GeomVertexFormat.getV3n3(),Geom.UHStatic)
    vertices=GeomVertexWriter(data,'vertex'); normals=GeomVertexWriter(data,'normal')
    triangles=GeomTriangles(Geom.UHStatic)
    index=0
    for face,normal in faces:
        for v in face:
            vertices.addData3(*v)
            normals.addData3(*(normal if normal is not None else v))
        for j in range(1,len(face)-1):triangles.addVertices(index,index+j,index+j+1)
        index+=len(face)
    geom=Geom(data);geom.addPrimitive(triangles)
    node=GeomNode(name);node.addGeom(geom)
    return NodePath(node)


def cylinder():
    faces=[];steps=32
    for i in range(steps):
        a=2*math.pi*i/steps;b=2*math.pi*(i+1)/steps
        ca,sa,cb,sb=math.cos(a),math.sin(a),math.cos(b),math.sin(b)
        faces.append(([(ca,sa,0),(cb,sb,0),(cb,sb,1),(ca,sa,1)],(math.cos((a+b)/2),math.sin((a+b)/2),0)))
        faces.append(([(0,0,1),(ca,sa,1),(cb,sb,1)],(0,0,1)))
        faces.append(([(0,0,0),(cb,sb,0),(ca,sa,0)],(0,0,-1)))
    return mesh('cylinder',faces)


def sphere():
    faces=[]
    def v(t,p):return (math.sin(t)*math.cos(p),math.sin(t)*math.sin(p),math.cos(t))
    for i in range(12):
        for j in range(24):
            t,p=math.pi*i/12,2*math.pi*j/24
            u,q=math.pi*(i+1)/12,2*math.pi*(j+1)/24
            faces.append(([v(t,p),v(u,p),v(u,q),v(t,q)],None))
    return mesh('sphere',faces)


def box():
    faces=[]
    for axis in range(3):
        u,v=(axis+1)%3,(axis+2)%3
        for sign in (-1,1):
            normal=[0,0,0];normal[axis]=sign
            corners=[]
            for a,b in ((-1,-1),(1,-1),(1,1),(-1,1)):
                point=[0,0,0];point[axis]=sign;point[u]=a;point[v]=b;corners.append(point)
            if sign<0:corners.reverse()
            faces.append((corners,normal))
    return mesh('rigid-box',faces)


class Renderer3D:
    def __init__(self,width=960,height=480):
        # Tk owns the Windows message loop. Letting Panda also consume messages
        # interrupts native title-bar drag/resize operations and snaps them back.
        loadPrcFileData('', 'disable-message-loop true')
        loadPrcFileData('',f'window-type offscreen\nwin-size {width} {height}\naudio-library-name null\nsync-video false\nnotify-level warning\n')
        from direct.showbase.ShowBase import ShowBase
        self.base=ShowBase(windowType='offscreen')
        if not self.base.win:raise RuntimeError('3D graphics context could not be created')
        self.base.disableMouse()
        self.base.setBackgroundColor(.06,.09,.13,1)
        self.base.camLens.setNearFar(.005,30)
        self.base.camLens.setFov(48)
        self.texture=Texture('observer-frame')
        self.base.win.addRenderTexture(self.texture,GraphicsOutput.RTMCopyRam,GraphicsOutput.RTPColor)
        self.width,self.height=width,height
        self.base.camLens.setAspectRatio(width/height)
        self.root=self.base.render.attachNewNode('observer-world')
        self.cylinder=cylinder();self.sphere=sphere();self.box=box()
        self.yaw=-65.;self.elevation=32.;self.distance=.85
        self.target=Vec3(.22,0,.015)
        ambient=AmbientLight('ambient');ambient.setColor((.43,.46,.52,1))
        self.root.setLight(self.root.attachNewNode(ambient))
        sun=DirectionalLight('sun');sun.setColor((.95,.91,.8,1))
        sun.setShadowCaster(True,2048,2048)
        sun.getLens().setFilmSize(1.5,1.5);sun.getLens().setNearFar(.1,8)
        self.sun=self.root.attachNewNode(sun)
        self.sun.setPos(-1,-2,3);self.sun.lookAt(.2,0,0)
        self.root.setLight(self.sun);self.root.setShaderAuto()
        floor=mesh('floor',[([(-10,-10,0),(10,-10,0),(10,10,0),(-10,10,0)],(0,0,1))])
        floor.reparentTo(self.root);floor.setColor(.18,.23,.29,1)
        self.floor=floor
        lines=LineSegs('10cm-grid');lines.setColor(.31,.39,.46,1);lines.setThickness(1)
        for i in range(-40,41):
            k=i*.1
            lines.moveTo(k,-4,.0004);lines.drawTo(k,4,.0004)
            lines.moveTo(-4,k,.0004);lines.drawTo(4,k,.0004)
        self.grid=self.root.attachNewNode(lines.create());self.grid.setLightOff();self.grid.setShaderOff()
        self.links=[];self.joints=[];self.pads=[];self.loads=[]
        for i in range(6):
            segment=self.cylinder.copyTo(self.root);segment.setColor(.16,.70,.58,1)
            self.links.append(segment)
        for i in range(7):
            joint=self.sphere.copyTo(self.root);joint.setColor(.73,.90,.86,1);joint.setScale(.009)
            self.joints.append(joint)
        for _ in range(2):
            pad=self.box.copyTo(self.root);pad.setScale(.022,.035,.019);self.pads.append(pad)
            load=self.cylinder.copyTo(self.root);load.setColor(.25,.65,1,1);self.loads.append(load)
        self.head=self.sphere.copyTo(self.root);self.head.setColor(.95,.72,.29,1)
        self.head.setScale(.012,.017,.011)
        self.sensor_face=self.root.attachNewNode('head-sensor-face')
        for row in range(3):
            for col in range(3):
                cell=self.sphere.copyTo(self.sensor_face);cell.setScale(.0025)
                cell.setPos((col-1)*.007,.009,(row-1)*.007);cell.setColor(.04,.06,.08,1)
        self.objects=[];self.object_signature=None;self.labels=[]
        self.trail_node=None

    def orbit(self,dx,dy):
        self.yaw += dx*.4
        self.elevation=max(8,min(85,self.elevation+dy*.3))

    def zoom(self,steps):self.distance=max(.22,min(5.,self.distance*math.exp(-steps*.12)))

    def home(self):self.yaw=-65.;self.elevation=32.;self.distance=.85;self.target=Vec3(.22,0,.015)

    def update_objects(self,objects):
        signature=tuple((o['kind'],o['x'],o['y'],o['radius'],o['height']) for o in objects)
        if signature!=self.object_signature:
            for node in self.objects+self.labels:node.removeNode()
            self.objects=[];self.labels=[];self.object_signature=signature
            for o in objects:
                node=self.cylinder.copyTo(self.root)
                node.setPos(o['x'],o['y'],0);node.setScale(o['radius'],o['radius'],o['height'])
                node.setColor(*{'food':(.34,.77,.44,1),'harm':(.82,.20,.28,1),'obstacle':(.46,.54,.64,1)}[o['kind']])
                self.objects.append(node)
                text=TextNode('label');text.setText(o['kind'].upper());text.setAlign(TextNode.ACenter)
                label=self.root.attachNewNode(text);label.setScale(.012);label.setPos(o['x'],o['y'],o['height']+.022)
                label.setBillboardPointEye();label.setLightOff();label.setShaderOff();self.labels.append(label)
        for o,node,label in zip(objects,self.objects,self.labels):
            if o.get('eaten'):node.hide();label.hide()
            else:node.show();label.show()

    def render(self,world,follow=True,trail=()):
        self.update_objects(world.objects)
        nodes=world.nodes
        m=world.mechanics
        # Three capsule bodies: a cylinder and two hemispherical ends each.
        from panda3d.core import Quat
        along_x=Quat();along_x.setFromAxisAngle(90,Vec3(0,1,0))
        for i,part in enumerate(m.segments):
            segment=self.links[i]
            segment.setPos(*m.point(part,(-.027,0,0)))
            segment.setQuat(along_x*part.getQuat());segment.setScale(.018,.018,.054)
            color=(.95,.72,.29,1) if i==2 else (.16,.70,.58,1)
            segment.setColor(*color)
            for j,x in enumerate((-.027,.027)):
                cap=self.joints[2*i+j];cap.setPos(*m.point(part,(x,0,0)));cap.setScale(.018);cap.setColor(*color)
        for link in self.links[3:]:link.hide()
        self.joints[6].hide();self.head.hide()
        for i,point in enumerate(m.feet()):
            self.pads[i].hide()  # No legs, feet, or standing platform.
            self.loads[i].setPos(point[0],point[1],point[2]+.04)
            self.loads[i].setScale(.0018,.0018,max(.00001,m.loads[i]*.05))
        face=m.point(m.segments[2],(.037,0,0))
        self.sensor_face.setPos(*face);self.sensor_face.setQuat(m.segments[2].getQuat())
        self.sensor_face.setH(self.sensor_face.getH()-90)
        if self.trail_node:self.trail_node.removeNode()
        if len(trail)>1:
            line=LineSegs('trail');line.setColor(.2,.9,.82,1);line.setThickness(2)
            for i,point in enumerate(trail[-600::3]):
                if i==0:line.moveTo(point[0],point[1],.001)
                else:line.drawTo(point[0],point[1],.001)
            self.trail_node=self.root.attachNewNode(line.create());self.trail_node.setLightOff();self.trail_node.setShaderOff()
        if follow:self.target=Vec3(*m.center())
        self.floor.setPos(self.target.x,self.target.y,0.)
        self.grid.setPos(round(self.target.x*10)/10,round(self.target.y*10)/10,0.)
        yaw=math.radians(self.yaw);elevation=math.radians(self.elevation)
        self.base.camera.setPos(self.target+Vec3(self.distance*math.cos(elevation)*math.cos(yaw),self.distance*math.cos(elevation)*math.sin(yaw),self.distance*math.sin(elevation)))
        self.base.camera.lookAt(self.target)
        self.sun.setPos(self.target+Vec3(-1,-2,3));self.sun.lookAt(self.target)
        self.base.graphicsEngine.renderFrame()
        raw=self.texture.getRamImageAs('RGB')
        if not raw:raise RuntimeError('3D renderer returned no frame')
        return Image.frombytes('RGB',(self.texture.getXSize(),self.texture.getYSize()),bytes(raw)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    def close(self):self.base.destroy()
