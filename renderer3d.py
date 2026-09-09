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


class Renderer3D:
    def __init__(self,width=960,height=480):
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
        self.cylinder=cylinder();self.sphere=sphere()
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
            segment=self.sphere.copyTo(self.root);segment.setColor(.16,.70,.58,1)
            self.links.append(segment)
        for i in range(7):
            joint=self.sphere.copyTo(self.root);joint.setColor(.73,.90,.86,1);joint.setScale(.009)
            self.joints.append(joint)
        for _ in range(2):
            pad=self.cylinder.copyTo(self.root);pad.setScale(.02,.025,.005);self.pads.append(pad)
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
        for i,(a,b) in enumerate(zip(nodes,nodes[1:])):
            segment=self.links[i]
            center=Point3(*[(a[k]+b[k])/2 for k in range(3)])
            # Cosmetic thickness sits above the physical centerline to keep feet readable.
            center.z+=.01
            segment.setPos(center);segment.lookAt(Point3(b[0],b[1],b[2]+.01))
            segment.setScale(.013,math.dist(a,b)/2,.012)
        for node,point in zip(self.joints,nodes):node.setPos(point[0],point[1],point[2]+.01)
        for i,index in enumerate((0,-1)):
            point=nodes[index];self.pads[i].setPos(point[0],point[1],.0006)
            self.pads[i].setColor(*((.20,.90,.66,1) if world.mechanics.states[i]=='stick' else (1,.57,.16,1)))
            self.loads[i].setPos(point[0],point[1],.03)
            self.loads[i].setScale(.0018,.0018,world.mechanics.loads[i]*.05)
        head=nodes[-1];heading=world.heading+world.angles[5]
        self.head.setPos(head[0],head[1],head[2]+.013)
        self.head.setH(math.degrees(heading)-90)
        self.sensor_face.setPos(head[0]+.009*math.cos(heading),head[1]+.009*math.sin(heading),head[2]+.013)
        self.sensor_face.setH(math.degrees(heading)-90)
        if self.trail_node:self.trail_node.removeNode()
        if len(trail)>1:
            line=LineSegs('trail');line.setColor(.2,.9,.82,1);line.setThickness(2)
            for i,point in enumerate(trail[-600::3]):
                if i==0:line.moveTo(point[0],point[1],.001)
                else:line.drawTo(point[0],point[1],.001)
            self.trail_node=self.root.attachNewNode(line.create());self.trail_node.setLightOff();self.trail_node.setShaderOff()
        if follow:self.target=Vec3((nodes[0][0]+nodes[-1][0])/2+.05,(nodes[0][1]+nodes[-1][1])/2,.025)
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
