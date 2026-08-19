# pip install grpcio==1.65.4 -i https://pypi.doubanio.com/simple
# pip install grpcio-tools

#!/bin/bash

# 指定输出目录
OUT_DIR="./"

# 创建输出目录（如果不存在）
mkdir -p "$OUT_DIR"

# 遍历当前目录及同级目录中的所有 .proto 文件
find . -maxdepth 1 -type f -name "*.proto" | while read -r proto_file; do
    echo "Processing $proto_file..."
    python -m grpc_tools.protoc --proto_path=. --python_out="$OUT_DIR" --grpc_python_out="$OUT_DIR" "$proto_file"
    if [[ $? -eq 0 ]]; then
        echo "Successfully generated code for $proto_file"
    else
        echo "Error generating code for $proto_file"
    fi
done

echo "All .proto files have been processed."
