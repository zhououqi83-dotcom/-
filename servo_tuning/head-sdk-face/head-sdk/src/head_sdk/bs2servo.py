import os
import yaml
from enum import Enum

# 参考映射函数
def map_range(x, from_min, from_max, to_min, to_max):
    x = max(min(x, from_max), from_min)
    from_range = from_max - from_min
    to_range = to_max - to_min
    return (x - from_min) * (to_range / from_range) + to_min
# 新映射函数，增加权重参数
def map_range_new(bs_data, weight, in_range, out_range):
    x = weight * max(min(bs_data, in_range[1]), in_range[0])
    from_range = weight * (in_range[1] - in_range[0])
    to_range = out_range[1] - out_range[0]
    out_data = weight * (x - in_range[0]) * (to_range / from_range) + out_range[0]
    return round(out_data, 3)
# 全局变量缓存映射规则
mapping_dict = None
# 加载 YAML 映射规则
def load_yaml_mapping(yaml_file=None):
    global mapping_dict
    if yaml_file is None:
        yaml_file = os.path.join(os.path.dirname(__file__), 'servo_mappings.yaml')
    with open(yaml_file, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
        mapping_dict = {}
        for servo, rules in config.items():
            mapping_dict[servo] = {}
            for bs_name, params in rules.items():
                weight, in_range, out_range = params
                mapping_dict[servo][bs_name] = (weight, in_range, out_range)
    return mapping_dict

def BStoServos(bs_dict, yaml_file=None):
    global mapping_dict
    if mapping_dict is None:
        load_yaml_mapping(yaml_file)
    bs_dict_lower = {k.lower(): v for k, v in bs_dict.items()}
    servo_data = {}
    for servo_name, rules in mapping_dict.items():
        for bs_name, params in rules.items():
            if bs_name.lower() in bs_dict_lower:
                bs_data = bs_dict_lower.get(bs_name.lower(), 0.0)
                weight, in_range, out_range = params
                if servo_name in servo_data :
                    servo_data[servo_name] =  servo_data[servo_name] + map_range_new(bs_data, weight, in_range, out_range)
                else :
                    servo_data[servo_name] = map_range_new(bs_data, weight, in_range, out_range)
                print(servo_name,"-----[",bs_name,"----",params,"]----",servo_data[servo_name])
    return servo_data

# if __name__ == '__main__':
#     # Test with sample blendshape dict
#     sample_bs = {f'bs_{i}': 0.1 for i in range(61)}
#     sample_bs.update({'EyeBlinkLeft': 0.5, 'HeadPitch': 0.3})

#     print('--- Testing YAML-based servo mapping ---')
#     result = BStoServos(sample_bs)
#     pprint(result)
#     print('\nDone.')


class FaceBlendShape52(Enum):
    # 眉毛5个自由度
    BrowDownLeft = 1
    BrowDownRight = 2
    BrowInnerUp = 3
    BrowOuterUpLeft = 4
    BrowOuterUpRight = 5
    
    # 脸颊 3个自由度
    CheekPuff = 6
    CheekSquintLeft = 7
    CheekSquintRight = 8
    
    # 眼睛 14个自由度
    EyeBlinkLeft = 9
    EyeBlinkRight = 10
    EyeLookDownLeft = 11
    EyeLookDownRight = 12
    EyeLookInLeft = 13
    EyeLookInRight = 14
    EyeLookOutLeft = 15
    EyeLookOutRight = 16
    EyeLookUpLeft = 17
    EyeLookUpRight = 18
    EyeSquintLeft = 19
    EyeSquintRight = 20
    EyeWideLeft = 21
    EyeWideRight = 22
    
    # 下颚 4个自由度
    JawForward = 23
    JawLeft = 24
    JawOpen = 25
    JawRight = 26
    
    # 嘴部 23个自由度
    MouthClose = 27
    MouthDimpleLeft = 28
    MouthDimpleRight = 29
    MouthFrownLeft = 30
    MouthFrownRight = 31
    MouthFunnel = 32
    MouthLeft = 33
    MouthLowerDownLeft = 34
    MouthLowerDownRight = 35
    MouthPressLeft = 36
    MouthPressRight = 37
    MouthPucker = 38
    MouthRight = 39
    MouthRollLower = 40
    MouthRollUpper = 41
    MouthShrugLower = 42
    MouthShrugUpper = 43
    MouthSmileLeft = 44
    MouthSmileRight = 45
    MouthStretchLeft = 46
    MouthStretchRight = 47
    MouthUpperUpLeft = 48
    MouthUpperUpRight = 49
    
    # 鼻子 2个自由度
    NoseSneerLeft = 50
    NoseSneerRight = 51

class FaceBlendShape61(Enum):
    # 眼部14个自由度
    EyeBlinkLeft = 0
    EyeLookDownLeft = 1
    EyeLookInLeft = 2
    EyeLookOutLeft = 3
    EyeLookUpLeft = 4
    EyeSquintLeft = 5
    EyeWideLeft = 6
    EyeBlinkRight = 7
    EyeLookDownRight = 8
    EyeLookInRight = 9
    EyeLookOutRight = 10
    EyeLookUpRight = 11
    EyeSquintRight = 12
    EyeWideRight = 13

    # 下颚4个自由度
    JawForward = 14
    JawLeft = 15
    JawRight = 16
    JawOpen = 17
    
    # 嘴部 23个自由度
    MouthClose = 18
    MouthFunnel = 19
    MouthPucker = 20
    MouthLeft = 21
    MouthRight = 22
    MouthSmileLeft = 23
    MouthSmileRight = 24
    MouthFrownLeft = 25
    MouthFrownRight = 26
    MouthDimpleLeft = 27
    MouthDimpleRight = 28
    MouthStretchLeft = 29
    MouthStretchRight = 30
    MouthRollLower = 31
    MouthRollUpper = 32
    MouthShrugLower = 33
    MouthShrugUpper = 34
    MouthPressLeft = 35
    MouthPressRight = 36

    MouthLowerDownLeft = 37
    MouthLowerDownRight = 38
    MouthUpperUpLeft = 39
    MouthUpperUpRight = 40

    # 眉毛5个自由度
    BrowDownLeft = 41
    BrowDownRight = 42
    BrowInnerUp = 43
    BrowOuterUpLeft = 44
    BrowOuterUpRight = 45
    
    CheekPuff = 46
    CheekSquintLeft = 47
    CheekSquintRight = 48
    NoseSneerLeft = 49
    NoseSneerRight = 50
    TongueOut = 51
    
    # 头部3个自由度
    HeadYaw = 52
    HeadPitch = 53
    HeadRoll = 54
    
    LeftEyeYaw = 55
    LeftEyePitch = 56
    LeftEyeRoll = 57
    RightEyeYaw = 58
    RightEyePitch = 59
    RightEyeRoll = 60

# 创建 52 到 61 的映射字典
blendshape_52_to_61_mapping = {
    # 眉毛 5 个自由度
    FaceBlendShape52.BrowDownLeft.name: FaceBlendShape61.BrowDownLeft.value,
    FaceBlendShape52.BrowDownRight.name: FaceBlendShape61.BrowDownRight.value,
    FaceBlendShape52.BrowInnerUp.name: FaceBlendShape61.BrowInnerUp.value,
    FaceBlendShape52.BrowOuterUpLeft.name: FaceBlendShape61.BrowOuterUpLeft.value,
    FaceBlendShape52.BrowOuterUpRight.name: FaceBlendShape61.BrowOuterUpRight.value,

    # 脸颊 3 个自由度
    FaceBlendShape52.CheekPuff.name: FaceBlendShape61.CheekPuff.value,
    FaceBlendShape52.CheekSquintLeft.name: FaceBlendShape61.CheekSquintLeft.value,
    FaceBlendShape52.CheekSquintRight.name: FaceBlendShape61.CheekSquintRight.value,

    # 眼睛 14 个自由度
    FaceBlendShape52.EyeBlinkLeft.name: FaceBlendShape61.EyeBlinkLeft.value,
    FaceBlendShape52.EyeLookDownLeft.name: FaceBlendShape61.EyeLookDownLeft.value,
    FaceBlendShape52.EyeLookInLeft.name: FaceBlendShape61.EyeLookInLeft.value,
    FaceBlendShape52.EyeLookOutLeft.name: FaceBlendShape61.EyeLookOutLeft.value,
    FaceBlendShape52.EyeLookUpLeft.name: FaceBlendShape61.EyeLookUpLeft.value,
    FaceBlendShape52.EyeSquintLeft.name: FaceBlendShape61.EyeSquintLeft.value,
    FaceBlendShape52.EyeWideLeft.name: FaceBlendShape61.EyeWideLeft.value,
    
    FaceBlendShape52.EyeBlinkRight.name: FaceBlendShape61.EyeBlinkRight.value,
    FaceBlendShape52.EyeLookDownRight.name: FaceBlendShape61.EyeLookDownRight.value,
    FaceBlendShape52.EyeLookInRight.name: FaceBlendShape61.EyeLookInRight.value,
    FaceBlendShape52.EyeLookOutRight.name: FaceBlendShape61.EyeLookOutRight.value,
    FaceBlendShape52.EyeLookUpRight.name: FaceBlendShape61.EyeLookUpRight.value,
    FaceBlendShape52.EyeSquintRight.name: FaceBlendShape61.EyeSquintRight.value,
    FaceBlendShape52.EyeWideRight.name: FaceBlendShape61.EyeWideRight.value,

    # 下颚 4 个自由度
    FaceBlendShape52.JawForward.name: FaceBlendShape61.JawForward.value,
    FaceBlendShape52.JawLeft.name: FaceBlendShape61.JawLeft.value,
    FaceBlendShape52.JawOpen.name: FaceBlendShape61.JawOpen.value,
    FaceBlendShape52.JawRight.name: FaceBlendShape61.JawRight.value,

    # 嘴部 23 个自由度
    FaceBlendShape52.MouthClose.name: FaceBlendShape61.MouthClose.value,
    FaceBlendShape52.MouthDimpleLeft.name: FaceBlendShape61.MouthDimpleLeft.value,
    FaceBlendShape52.MouthDimpleRight.name: FaceBlendShape61.MouthDimpleRight.value,
    FaceBlendShape52.MouthFrownLeft.name: FaceBlendShape61.MouthFrownLeft.value,
    FaceBlendShape52.MouthFrownRight.name: FaceBlendShape61.MouthFrownRight.value,
    FaceBlendShape52.MouthFunnel.name: FaceBlendShape61.MouthFunnel.value,
    FaceBlendShape52.MouthLeft.name: FaceBlendShape61.MouthLeft.value,
    FaceBlendShape52.MouthLowerDownLeft.name: FaceBlendShape61.MouthLowerDownLeft.value,
    FaceBlendShape52.MouthLowerDownRight.name: FaceBlendShape61.MouthLowerDownRight.value,
    FaceBlendShape52.MouthPressLeft.name: FaceBlendShape61.MouthPressLeft.value,
    FaceBlendShape52.MouthPressRight.name: FaceBlendShape61.MouthPressRight.value,
    FaceBlendShape52.MouthPucker.name: FaceBlendShape61.MouthPucker.value,
    FaceBlendShape52.MouthRight.name: FaceBlendShape61.MouthRight.value,
    FaceBlendShape52.MouthRollLower.name: FaceBlendShape61.MouthRollLower.value,
    FaceBlendShape52.MouthRollUpper.name: FaceBlendShape61.MouthRollUpper.value,
    FaceBlendShape52.MouthShrugLower.name: FaceBlendShape61.MouthShrugLower.value,
    FaceBlendShape52.MouthShrugUpper.name: FaceBlendShape61.MouthShrugUpper.value,
    FaceBlendShape52.MouthSmileLeft.name: FaceBlendShape61.MouthSmileLeft.value,
    FaceBlendShape52.MouthSmileRight.name: FaceBlendShape61.MouthSmileRight.value,
    FaceBlendShape52.MouthStretchLeft.name: FaceBlendShape61.MouthStretchLeft.value,
    FaceBlendShape52.MouthStretchRight.name: FaceBlendShape61.MouthStretchRight.value,
    FaceBlendShape52.MouthUpperUpLeft.name: FaceBlendShape61.MouthUpperUpLeft.value,
    FaceBlendShape52.MouthUpperUpRight.name: FaceBlendShape61.MouthUpperUpRight.value,

    # 鼻子 2 个自由度
    FaceBlendShape52.NoseSneerLeft.name: FaceBlendShape61.NoseSneerLeft.value,
    FaceBlendShape52.NoseSneerRight.name: FaceBlendShape61.NoseSneerRight.value,
}


def map_52_to_61(blendshape_52_values):
    global blendshape_52_to_61_mapping
    """
    将 52 个 blendshape 参数映射到 61 名称一致的数组中。

    参数:
    blendshape_52_values (list): 长度为 52 的数组，包含 52 blendshape 的参数值。

    返回:
    list: 长度为 61 的数组，对应 61 blendshape 名称的参数值，没有对应值的填充为 None。
    """
    if len(blendshape_52_values) != 52:
        raise ValueError("输入的数组长度必须为 52")
    
    # 初始化结果数组为 None
    blendshape_61_values = [0] * 61
    
    # 遍历 52 的值，将其映射到 61
    for blendshape_52, value in zip(FaceBlendShape52, blendshape_52_values):
        if blendshape_52.name != "Basic":
            blendshape_61_index = blendshape_52_to_61_mapping.get(blendshape_52.name)
            if blendshape_61_index is not None:
                blendshape_61_values[blendshape_61_index] = value
        
    return blendshape_61_values
def get_bs_dict(data_in):
    blendshape_dict = {}
    if len(data_in) == 52:
        for shape in FaceBlendShape52:
            blendshape_dict[shape.name] = data_in[shape.value]
        rpy_angles = {"rpy_0":0.5, "rpy_1":0.5, "rpy_2":0.5}
        return {**blendshape_dict, **rpy_angles}
    if len(data_in) == 61:
        for shape in FaceBlendShape61:
            blendshape_dict[shape.name] = data_in[shape.value]
        return blendshape_dict

def get_bs_dict_PyLive(live_link_face):
    blend_shapes = {}
    try:
        # 定义所有需要获取的BlendShape
        for shape in FaceBlendShape61:
            blend_shapes[shape.name] = live_link_face.get_blendshape(shape)
    except Exception as e:
        print(f"获取BlendShape时出错: {e}")
        return None

    return blend_shapes


# servo dict
class HeadServo(Enum):
        left_blink = 1        
        left_eye_erect = 2     
        left_eye_level = 3    
        left_eyebrow_erect = 4
        left_eyebrow_level = 5

        right_blink = 6       
        right_eye_erect = 7   
        right_eye_level = 8   
        right_eyebrow_erect = 9
        right_eyebrow_level = 10

        head_dian = 11 
        head_yao = 12          
        head_bai = 13        

def get_servo_head_dict(data_in):
    get_dict = {}
    for shape in HeadServo:
        get_dict[shape.name] = round(data_in[shape.value - 1], 4)
    return get_dict

class MouthServo(Enum):
        mouthUpperUpLeft = 1        
        mouthUpperUpRight = 2     
        mouthLowerDownLeft = 3    
        mouthLowerDownRight = 4

        mouthCornerUpLeft = 5
        mouthCornerUpRight = 6       
        mouthCornerDownLeft = 7   
        mouthCornerDownRight = 8   

        jawFrontLeft = 9
        jawFrontRight = 10
        jawBackLeft = 11 
        jawBackRight = 12          

def get_servo_mouth_dict(data_in):
    get_dict = {}
    for shape in MouthServo:
        get_dict[shape.name] = data_in[shape.value - 1]
    return get_dict

