from setuptools import setup, find_packages

setup(
    name="head-sdk",
    version="0.2.0",
    description="Droid Robot Head SDK",
    author="Droid Robot, Inc.",
    author_email="support@droidrobot.com",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,          # ← 重要
    package_data={
        "head_sdk": ["*.yaml", "*.yml"],   # ← 把 servo_mappings.yaml 包含进来
        # 如果你以后还有其他数据文件，也可以写 "head_sdk": ["*.yaml", "data/*.json"] 等
    },
    install_requires=[
        "grpcio==1.65.4",
        "grpcio-tools==1.65.4",
        "protobuf==5.29.4",
        "rena2_sdk_api==0.1.0",
        "pyyaml"
    ],
)
