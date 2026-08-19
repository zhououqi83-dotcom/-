import yaml
import serial
import time
import threading

class Servo:
    """A simple class to hold servo configuration data."""
    def __init__(self, name, id, jdStart, jdMax, jdMin, fScale, fOffSet, pos, dir):
        self.name = name
        self.id = id
        self.jdStart = jdStart
        self.jdMax = jdMax
        self.jdMin = jdMin
        self.fScale = fScale
        self.fOffSet = fOffSet
        self.pos = pos
        self.dir = dir
        # Calculate the initial position on a 0.0 to 1.0 scale
        self.jdInitOne = round(abs((jdStart - jdMin) / (jdMax - jdMin)), 2)
        if self.dir == 1:
            self.jdInitOne = round((1 - self.jdInitOne), 2)
    def __repr__(self):
        return f"Servo(name='{self.name}', id={self.id})"

class SerialServoController:
    """
    Manages a single serial port and the servos connected to it.
    """
    def __init__(self, port, baudrate, servo_configs):
        self.port_name = port
        self.baudrate = baudrate
        self.serial_conn = None
        self.start_reconnect = None
        
        # Create Servo objects from the configuration
        self.servos         = {config['name']: Servo(**config) for config in servo_configs}
        self.servos_init    = {name: self.servos[name].jdInitOne for name in self.servos.keys()}
        self.servos_current = {} 
        self.servos_targets = {}

    def connect(self):
        """Establishes the serial connection."""
        try:
            self.serial_conn = serial.Serial(self.port_name, self.baudrate)
            print(f"Successfully connected to port: {self.port_name}")
            return True
        except serial.SerialException as e:
            print(f"Error: Failed to connect to port {self.port_name}. {e}")
            print("Running in simulation mode. No data will be sent.")
            self.serial_conn = None 
            return False

    def set_servo_value(self, servo_name, value):
        """Sets the target value for a specific servo."""
        # Correctly checks if the servo exists on this controller
        if servo_name in self.servos:
            # Clamp value between 0.0 and 1.0 and update the target
            self.servos_targets[servo_name] = max(0.0, min(1.0, value))
        else:
            # This case should ideally not be hit if using the UniversalRobot class
            print(f"Warning: Servo '{servo_name}' not found on controller for port {self.port_name}")

    def send_commands(self):
        """
        Constructs and sends the command frame for all servos on this serial port.
        """
        if not self.serial_conn:
            return # Don't send if not connected

        head = 0xaa
        end = 0x2f
        frameData = [head, 0x00] # Start with servo_num = 0
        servo_num = 0
        for name, target_value in self.servos_targets.items():
            if name in self.servos_current :
                if self.servos_current[name] == target_value:
                    continue
            self.servos_current[name] = target_value
            # print(name, target_value)
            servo = self.servos[name]
            msg = (1 - servo.dir) * target_value + servo.dir * (1 - target_value)
            node = servo.jdMin + msg * (servo.jdMax - servo.jdMin)
            node = max(servo.jdMin, min(servo.jdMax, node))
            
            if abs(node - servo.pos) > 1e-5: # Compare floats with a tolerance
                servo.pos = node
                node_scaled = int((node + servo.fOffSet) * servo.fScale)
                pos_l = node_scaled & 0xFF
                pos_h = (node_scaled >> 8) & 0xFF
                id = servo.id & 0xFF
                frameData.extend([id, pos_h, pos_l])
                servo_num += 1
        
        if servo_num > 0:
            frameData[1] = servo_num
            frameData.append(end)
            
            # print(f"Sending to {self.port_name}: {bytes(frameData).hex(' ')}")
            self.serial_conn.write(bytes(frameData))
        self.servos_targets = {}

    def close(self):
        """Closes the serial connection."""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            print(f"Connection to {self.port_name} closed.")
    
    def _check_thread(self):
        while True:
            time.sleep(0.1) # Check every second
            flag = self.connect()
            if flag:
                self.start_reconnect = None
                break
    
    def reconnect(self):
        if self.start_reconnect is None:
            self.start_reconnect = threading.Thread(target=self._check_thread, daemon=True)
            self.start_reconnect.start()



class UniversalRobot:
    """
    A generic robot class that loads its configuration from a YAML file
    and manages one or more serial controllers.
    """
    def __init__(self, config_file, baudrate=9600):
        self.controllers = []
        self.servo_to_controller_map = {}
        
        self._load_config(config_file, baudrate)

    def _load_config(self, config_file, baudrate):
        """Loads a YAML config and initializes controllers."""
        print(f"--- Loading configuration from: {config_file} ---")
        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            print(f"Error: Configuration file not found at '{config_file}'")
            return

        if 'controllers' not in config or not isinstance(config['controllers'], list):
            print("Error: YAML file must contain a 'controllers' list.")
            return

        for controller_config in config['controllers']:
            port = controller_config['port']
            servos = controller_config['servos']
            
            controller = SerialServoController(port, baudrate, servos)
            self.controllers.append(controller)
            
            for servo_name in controller.servos.keys():
                if servo_name in self.servo_to_controller_map:
                    print(f"Warning: Duplicate servo name '{servo_name}' found. Check your YAML files.")
                self.servo_to_controller_map[servo_name] = controller
        
        print(f"Configuration loaded. Found {len(self.controllers)} serial controller(s).")
        print(f"Total servos configured: {len(self.servo_to_controller_map)}")

    def connect_all(self):
        """Connects all configured serial controllers."""
        for controller in self.controllers:
            controller.connect()
    
    def reconnect(self):
        """Reconnects all configured serial controllers."""
        for controller in self.controllers:
            controller.reconnect()

    def set_servos(self, servo_values_dict):
        """
        Finds the correct controller for each servo in the dictionary and sets its value.
        """
        for servo_name, value in servo_values_dict.items():
            if servo_name in self.servo_to_controller_map:
                controller = self.servo_to_controller_map[servo_name]
                controller.set_servo_value(servo_name, value)
            else:
                print(f"Error: Servo '{servo_name}' is not defined in the loaded configuration.")
        self.update_all_servos()

    def get_robot_status(self):
        """
        Collects and returns a dictionary of the current target values for all servos.
        """
        robot_status = {}
        for controller in self.controllers:
            robot_status.update(controller.servos_current)
        return robot_status

    def get_robot_init(self):
        """
        Collects and returns a dictionary of the current target values for all servos.
        """
        robot_status = {}
        for controller in self.controllers:
            robot_status.update(controller.servos_init)
        return robot_status

    def update_all_servos(self):
        """
        Sends the command frames for all controllers.
        This should be called in a loop.
        """
        for controller in self.controllers:
            controller.send_commands()

    def close_all(self):
        """Closes all serial connections."""
        for controller in self.controllers:
            controller.close()

# --- Main Execution Example ---
if __name__ == "__main__":
    # CHOOSE WHICH ROBOT CONFIGURATION TO LOAD
    config_to_load = 'servo_config_v3.yaml' # For single-port robot
    # config_to_load = 'servo_config_v2.yaml' # For multi-port robot

    # 1. Initialize the robot from the config file
    robot = UniversalRobot(config_file=config_to_load, baudrate=9600)
    
    # 2. Connect to the serial ports
    robot.connect_all()

    # 3. Example of getting the initial robot status
    print("\n--- Initial Robot Status ---")
    initial_status = robot.get_robot_status()
    print(initial_status)

    # 4. Example of controlling servos using a dictionary
    print("\n--- Controlling Servos with a Dictionary ---")
    new_targets = {
        'left_blink': 0.9,
        'mouthUpperUpLeft': 0.2,
        'non_existent_servo': 0.5 # Example of an error
    }
    robot.set_servos(new_targets)

    # 5. Get the status after setting new values
    print("\n--- Robot Status After Update ---")
    updated_status = robot.get_robot_status()
    print(updated_status)

    # 6. In a real application, you would have a loop
    try:
        print("\n--- Starting update loop for 5 seconds (press Ctrl+C to exit) ---")
        for i in range(50):
            # You can continuously update servo values here
            blink_value = (i % 20) / 19.0 # Make the eye blink
            robot.set_servos({'left_blink': blink_value})
            
            robot.update_all_servos()
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nExiting loop.")
    finally:
        # 7. Make sure to close connections
        robot.close_all()