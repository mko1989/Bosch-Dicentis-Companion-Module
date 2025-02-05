import os
import sys
import logging
import asyncio
import json
import traceback
from dotenv import load_dotenv


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('dicentis_osc_bridge.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def check_configuration():
    """Check if configuration exists, launch config GUI if not"""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    
    # If .env file doesn't exist or is empty, launch config GUI
    if not os.path.exists(env_path) or os.path.getsize(env_path) == 0:
        logger.warning("No configuration found. Launching configuration GUI.")
        launch_config_gui()
        return False
    
    # Load environment variables
    load_dotenv(env_path)
    
    # Check for critical configuration variables
    critical_vars = [
        'DICENTIS_SERVER_IP', 
        'DICENTIS_USERNAME', 
        'DICENTIS_PASSWORD', 
        'OSC_TARGET_IP', 
        'OSC_TARGET_PORT', 
        'LOCAL_OSC_PORT'
    ]
    
    missing_vars = [var for var in critical_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.warning(f"Missing configuration variables: {', '.join(missing_vars)}")
        launch_config_gui()
        return False
    
    return True

import os
import json
import logging
import asyncio
import ssl
import socket
import threading
import traceback
import psutil
import ipaddress
import websockets
from concurrent.futures import ThreadPoolExecutor
from pythonosc import udp_client, dispatcher, osc_server

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('dicentis_osc_bridge.log', mode='w'),  # Overwrite log each time
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DicentisOscBridge:
    def __init__(self):
        # Load configuration from JSON
        config_path = os.path.join(os.path.dirname(__file__), 'config.json')
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except FileNotFoundError:
            logger.error("Configuration file not found. Please run config_gui.py first.")
            raise

        # Server configuration
        self.server_ip = config['dicentis_server_ip']
        self.username = config['dicentis_username']
        self.password = config['dicentis_password']

        # OSC configuration
        self.osc_target_ip = config['osc_target_ip']
        self.osc_target_port = int(config['osc_target_port'])
        self.local_osc_port = int(config['local_osc_port'])

        # Network diagnostics
        self.network_diagnostics()

        # Create SSL context that doesn't verify certificates
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

        # Initialize WebSocket and OSC components
        self.websocket = None
        
        # Manually add seat details for testing
        self.seat_info = config.get('seats', {})
        
        self.seat_details = {}  # Store full seat details for mic control
        self.last_discussion_data = None  # Store last discussion data for mic state checking
        
        # Create OSC client for sending messages
        self.osc_client = udp_client.SimpleUDPClient(self.osc_target_ip, self.osc_target_port)
        
        # Create OSC dispatcher for receiving messages
        self.osc_dispatcher = dispatcher.Dispatcher()
        
        # Register OSC message handlers with extensive logging
        self.register_osc_handlers()
        
        # OSC server components
        self.osc_server = None
        self.osc_server_thread = None
        self.osc_server_stop_event = threading.Event()

        # Create a thread pool for running async tasks
        self.thread_pool = ThreadPoolExecutor(max_workers=5)
        
        # Create a new event loop for async tasks
        self.async_loop = None
        self.async_thread = None

    def start_async_event_loop(self):
        """Start a separate thread with its own event loop"""
        def run_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        # Create a new event loop
        self.async_loop = asyncio.new_event_loop()
        
        # Start the event loop in a separate thread
        self.async_thread = threading.Thread(
            target=run_loop, 
            args=(self.async_loop,), 
            daemon=True
        )
        self.async_thread.start()

    def stop_async_event_loop(self):
        """Stop the async event loop"""
        if self.async_loop:
            self.async_loop.call_soon_threadsafe(self.async_loop.stop)
            if self.async_thread:
                self.async_thread.join(timeout=5)

    def run_async_task(self, coro):
        """Run an async task in the dedicated async thread"""
        try:
            # Use call_soon_threadsafe to schedule the coroutine
            future = asyncio.run_coroutine_threadsafe(coro, self.async_loop)
            return future.result()
        except Exception as e:
            logger.error(f"Error running async task: {e}", exc_info=True)
            print(f"Error running async task: {e}")

    def handle_mic_activate_wrapper(self, address, *args):
        """Wrapper for mic activate with extensive logging"""
        logger.info(f"RECEIVED MIC ACTIVATE - Address: {address}, Args: {args}")
        print(f"RECEIVED MIC ACTIVATE - Address: {address}, Args: {args}")
        try:
            # Ensure args is not empty
            screen_line = args[0] if args else None
            if not screen_line:
                logger.error("No screen line provided for mic activation")
                return

            # Find seat info
            seat_info = self.get_seat_info_by_screen_line(screen_line)
            
            if seat_info:
                # If seat found, call activate method
                self.run_async_task(
                    self.activate_microphone(seat_info)
                )
            else:
                logger.error(f"Cannot activate mic: No seat found for {screen_line}")
        
        except Exception as e:
            logger.error(f"Error in mic activate handler: {e}", exc_info=True)
            print(f"Error in mic activate handler: {e}")

    def handle_mic_deactivate_wrapper(self, address, *args):
        """Wrapper for mic deactivate with extensive logging"""
        logger.info(f"RECEIVED MIC DEACTIVATE - Address: {address}, Args: {args}")
        print(f"RECEIVED MIC DEACTIVATE - Address: {address}, Args: {args}")
        try:
            # Ensure args is not empty
            screen_line = args[0] if args else None
            if not screen_line:
                logger.error("No screen line provided for mic deactivation")
                return

            # Find seat info
            seat_info = self.get_seat_info_by_screen_line(screen_line)
            
            if seat_info:
                # If seat found, call deactivate method
                self.run_async_task(
                    self.deactivate_microphone(seat_info)
                )
            else:
                logger.error(f"Cannot deactivate mic: No seat found for {screen_line}")
        
        except Exception as e:
            logger.error(f"Error in mic deactivate handler: {e}", exc_info=True)
            print(f"Error in mic deactivate handler: {e}")

    def handle_mic_control(self, address, *args):
        """Handle microphone toggle via OSC"""
        try:
            logger.debug(f"Mic Control - Received OSC message: Address={address}, Args={args}")
            
            screen_line = args[0] if args else None
            if not screen_line:
                logger.error("No screen line provided for microphone control")
                return

            logger.debug(f"Attempting to toggle microphone for screen line: {screen_line}")
            
            # Find seat info for the given screen line
            seat_info = self.get_seat_info_by_screen_line(screen_line)
            if not seat_info:
                logger.error(f"No seat found for screen line: {screen_line}")
                logger.debug(f"Current seat details: {self.seat_info}")
                return

            logger.debug(f"Found seat info: {seat_info}")
            
            # Determine current microphone state from discussion list
            current_mic_state = self.get_current_mic_state(screen_line)
            logger.debug(f"Current microphone state for {screen_line}: {current_mic_state}")
            
            # Toggle microphone based on current state
            if current_mic_state == 'on':
                # Microphone is on, so deactivate it
                logger.debug(f"Deactivating microphone for {screen_line}")
                self.run_async_task(
                    self.deactivate_microphone(seat_info)
                )
            else:
                # Microphone is off, so activate it
                logger.debug(f"Activating microphone for {screen_line}")
                self.run_async_task(
                    self.activate_microphone(seat_info)
                )
        
        except Exception as e:
            logger.error(f"Detailed error toggling microphone: {e}", exc_info=True)

    def run_osc_server(self):
        """Run OSC server in a separate thread"""
        # Start the async event loop thread first
        self.start_async_event_loop()
        
        # Then start the OSC server thread
        self.osc_server_stop_event.clear()
        self.osc_server_thread = threading.Thread(target=self.start_osc_server, daemon=True)
        self.osc_server_thread.start()

    def stop_osc_server(self):
        """Stop the OSC server"""
        # Stop the async event loop first
        self.stop_async_event_loop()
        
        # Then stop the OSC server thread
        if self.osc_server_thread and self.osc_server_thread.is_alive():
            self.osc_server_stop_event.set()
            self.osc_server_thread.join(timeout=5)
            
            if self.osc_server_thread.is_alive():
                logger.warning("OSC server thread did not stop gracefully")
                print("OSC server thread did not stop gracefully")

    def register_osc_handlers(self):
        """Register OSC message handlers"""
        # Test OSC handler
        self.osc_dispatcher.map("/test/osc", self.default_osc_handler)
        logger.info("Registering OSC handler for /test/osc")
        print("Registering OSC handler for /test/osc")

        # Microphone control handlers
        self.osc_dispatcher.map("/dicentis/mic/control", self.handle_mic_control)
        logger.info("Registering OSC handler for /dicentis/mic/control")
        print("Registering OSC handler for /dicentis/mic/control")

        # Microphone activation and deactivation handlers
        self.osc_dispatcher.map("/dicentis/mic/activate", self.handle_mic_activate_wrapper)
        logger.info("Registering OSC handler for /dicentis/mic/activate")
        print("Registering OSC handler for /dicentis/mic/activate")

        self.osc_dispatcher.map("/dicentis/mic/deactivate", self.handle_mic_deactivate_wrapper)
        logger.info("Registering OSC handler for /dicentis/mic/deactivate")
        print("Registering OSC handler for /dicentis/mic/deactivate")

    def default_osc_handler(self, address, *args):
        """Default handler for all incoming OSC messages with extensive logging"""
        logger.info(f"RECEIVED OSC MESSAGE - Address: {address}, Args: {args}")
        print(f"RECEIVED OSC MESSAGE - Address: {address}, Args: {args}")

    def network_diagnostics(self):
        """Perform comprehensive network diagnostics"""
        try:
            # Get all network interfaces
            logger.info("Network Interfaces:")
            for interface, addresses in psutil.net_if_addrs().items():
                logger.info(f"Interface: {interface}")
                for addr in addresses:
                    if addr.family == socket.AF_INET:
                        logger.info(f"  IPv4 Address: {addr.address}")
                        logger.info(f"  Netmask: {addr.netmask}")
                        logger.info(f"  Broadcast IP: {addr.broadcast}")

            # Check listening ports
            logger.info("\nListening Ports:")
            for conn in psutil.net_connections():
                if conn.status == psutil.CONN_LISTEN:
                    logger.info(f"Port {conn.laddr.port} is listening")

            # Validate OSC configuration
            logger.info("\nOSC Configuration Validation:")
            logger.info(f"Local OSC Port: {self.local_osc_port}")
            logger.info(f"Target OSC IP: {self.osc_target_ip}")
            logger.info(f"Target OSC Port: {self.osc_target_port}")

            # Validate IP addresses
            try:
                ipaddress.ip_address(self.local_osc_port)
                logger.warning("Local OSC port looks like an IP address. This might be incorrect.")
            except ValueError:
                pass

            try:
                ipaddress.ip_address(self.osc_target_ip)
            except ValueError:
                logger.warning(f"Invalid target IP: {self.osc_target_ip}")

        except Exception as e:
            logger.error(f"Network diagnostics error: {e}")
            logger.error(traceback.format_exc())

    def start_osc_server(self):
        """Start OSC server to receive microphone control commands"""
        try:
            # Try to release the port if it's in use
            self.release_port(self.local_osc_port)
            
            logger.critical(f"ATTEMPTING TO START OSC SERVER ON 0.0.0.0:{self.local_osc_port}")
            print(f"ATTEMPTING TO START OSC SERVER ON 0.0.0.0:{self.local_osc_port}")
            
            # Create a custom UDP server that allows port reuse
            class ReuseUDPServer(osc_server.ThreadingOSCUDPServer):
                allow_reuse_address = True

            server = ReuseUDPServer(
                ("0.0.0.0", self.local_osc_port),  
                self.osc_dispatcher
            )
            self.osc_server = server
            
            logger.critical(f"OSC SERVER STARTED SUCCESSFULLY ON PORT {self.local_osc_port}")
            print(f"OSC SERVER STARTED SUCCESSFULLY ON PORT {self.local_osc_port}")
            
            # Log all registered handlers
            logger.critical("REGISTERED OSC HANDLERS:")
            print("REGISTERED OSC HANDLERS:")
            
            # Manually get handlers from the dispatcher
            for pattern, handler in self.osc_dispatcher._map.items():
                logger.critical(f"  - {pattern}: {handler}")
                print(f"  - {pattern}: {handler}")
            
            # Run server until stop event is set
            while not self.osc_server_stop_event.is_set():
                try:
                    server.handle_request()
                except Exception as req_error:
                    logger.critical(f"ERROR IN OSC SERVER REQUEST HANDLING: {req_error}")
                    print(f"ERROR IN OSC SERVER REQUEST HANDLING: {req_error}")
                    traceback.print_exc()
            
            server.server_close()
            logger.critical("OSC SERVER STOPPED GRACEFULLY")
            print("OSC SERVER STOPPED GRACEFULLY")
        
        except Exception as e:
            logger.critical(f"FATAL ERROR STARTING OSC SERVER: {e}")
            print(f"FATAL ERROR STARTING OSC SERVER: {e}")
            traceback.print_exc()
            raise

    def release_port(self, port):
        """Attempt to release a port that might be in use"""
        try:
            # Find and terminate processes using the port
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    # Get network connections for the process
                    proc_connections = proc.connections()
                    for conn in proc_connections:
                        if conn.laddr and conn.laddr.port == port:
                            logger.critical(f"TERMINATING PROCESS {proc.info['name']} (PID {proc.info['pid']}) USING PORT {port}")
                            print(f"TERMINATING PROCESS {proc.info['name']} (PID {proc.info['pid']}) USING PORT {port}")
                            proc.terminate()
                except Exception as proc_error:
                    logger.critical(f"ERROR CHECKING PROCESS: {proc_error}")
                    print(f"ERROR CHECKING PROCESS: {proc_error}")
        except Exception as e:
            logger.critical(f"PORT RELEASE ERROR: {e}")
            print(f"PORT RELEASE ERROR: {e}")

    def get_current_mic_state(self, screen_line):
        """Get current microphone state for a given screen line"""
        try:
            # Get the most recent discussion list data
            if not self.last_discussion_data:
                logger.debug("No recent discussion data available")
                return 'off'
            
            # Find the seat in the discussion list
            for discussion in self.last_discussion_data.get('parameters', {}).get('discussionList', []):
                logger.debug(f"Checking discussion: {discussion}")
                if discussion.get('screenLine') == screen_line:
                    mic_state = discussion.get('microphoneState', 'off')
                    logger.debug(f"Found mic state for {screen_line}: {mic_state}")
                    return mic_state
            
            # If not found in discussion list, assume it's off
            logger.debug(f"No mic state found for {screen_line}, assuming off")
            return 'off'
        
        except Exception as e:
            logger.error(f"Error getting microphone state for {screen_line}: {e}", exc_info=True)
            return 'off'

    async def connect(self):
        """Establish WebSocket connection to Dicentis server"""
        uri = f"wss://{self.server_ip}:31416/Dicentis/API"
        self.websocket = await websockets.connect(
            uri, 
            subprotocols=["DICENTIS_1_0"],  # Explicitly specify the subprotocol
            ssl=self.ssl_context
        )
        
        # Login to the server
        await self.login()
        
        # Retrieve seat information once at connection
        await self.retrieve_seat_info()

    async def login(self):
        """Login to the Dicentis server"""
        login_request = json.dumps({
            "operation": "login",
            "parameters": {
                "user": self.username,
                "password": self.password
            }
        })
        await self.websocket.send(login_request)
        response = await self.websocket.recv()
        logger.info("Login Response: " + response)
        
        # Parse login response and check for errors
        try:
            login_data = json.loads(response)
            if login_data.get("operation") == "error":
                error_message = login_data.get("parameters", {}).get("message", "Unknown login error")
                logger.critical(f"Login failed: {error_message}")
                logger.critical(f"Username used: {self.username}")
                print(f"Login failed. Check username and permissions. Error: {error_message}")
                raise RuntimeError(f"Login failed: {error_message}")
        except json.JSONDecodeError:
            logger.error("Failed to parse login response")
            raise

    async def retrieve_seat_info(self):
        """Retrieve seat information once at connection"""
        seats_data = await self.get_seats_info()
        await self.process_seats_info(seats_data)

    async def get_seats_info(self):
        """Retrieve seats information from the server"""
        seats_request = json.dumps({
            "operation": "getseats",
            "parameters": {}
        })
        await self.websocket.send(seats_request)
        response = await self.websocket.recv()
        
        # Log the full raw response for debugging
        logger.info("Raw Seats Response:")
        logger.info(response)
        
        try:
            seats_data = json.loads(response)
            
            # Check for error response
            if seats_data.get("operation") == "error":
                error_message = seats_data.get("parameters", {}).get("message", "Unknown seats retrieval error")
                logger.critical(f"Seats retrieval failed: {error_message}")
                print(f"Failed to retrieve seats. Error: {error_message}")
            
            # Log the parsed seats data
            logger.info("Parsed Seats Response:")
            logger.info(json.dumps(seats_data, indent=2))
            
            return seats_data
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding seats response: {e}")
            return {"parameters": {"seats": []}}

    async def get_discussion_list(self):
        """Retrieve discussion list from the server"""
        discussion_request = json.dumps({
            "operation": "GetDiscussionList",
            "parameters": {}
        })
        await self.websocket.send(discussion_request)
        response = await self.websocket.recv()
        
        # Log the full raw response for debugging
        logger.info("Raw Discussion List Response:")
        logger.info(response)
        
        try:
            discussion_data = json.loads(response)
            
            # Check for error response
            if discussion_data.get("operation") == "error":
                error_message = discussion_data.get("parameters", {}).get("message", "Unknown discussion list error")
                logger.critical(f"Discussion list retrieval failed: {error_message}")
                print(f"Failed to retrieve discussion list. Error: {error_message}")
            
            # Log the parsed discussion data
            logger.info("Parsed Discussion List Response:")
            logger.info(json.dumps(discussion_data, indent=2))
            
            return discussion_data
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding discussion list response: {e}")
            return {"parameters": {"discussionList": []}}

    async def process_discussion_list(self, discussion_data):
        """Process discussion list and send OSC messages for microphone states"""
        discussion_list = discussion_data.get('parameters', {}).get('discussionList', [])
        
        for seat in discussion_list:
            screen_line = seat.get('screenLine', '')
            mic_state = seat.get('microphoneState', '')
            
            # Send microphone state OSC message
            if mic_state:
                osc_address = f"/custom-variable/mic_on/value"
                logger.debug(f"Sending OSC message - Address: {osc_address}, Value: {screen_line}")
                self.osc_client.send_message(osc_address, screen_line)

    async def process_seats_info(self, seats_data):
        """Process seats information"""
        seats = seats_data.get('parameters', {}).get('seats', [])
        
        # Store seat information keyed by seat number
        self.seat_info = {}
        self.seat_details = {}  # Reset seat details
        
        for seat in seats:
            # Use seatName as the seat number, screenLine as the value
            seat_name = seat.get('seatName', '')
            screen_line = seat.get('screenLine', '')
            
            # Skip seats with no name or hidden seats
            if not seat_name or seat.get('hideSeat', False):
                continue
            
            # Use seat number as the key
            try:
                seat_number = int(''.join(filter(str.isdigit, seat_name)))
            except ValueError:
                # If can't convert to int, skip this seat
                continue
            
            # Store full seat details for mic control
            seat_details = {
                'seatName': seat_name,
                'screenLine': screen_line,
                'seatId': seat.get('seatId', ''),
                'participantId': seat.get('seatedParticipantId', '')
            }
            
            self.seat_info[seat_number] = {
                'seatName': seat_name,
                'screenLine': screen_line
            }
            
            # Store full details by screen line for easy lookup
            self.seat_details[screen_line] = seat_details
        
        logger.info(f"Retrieved seat info for {len(self.seat_info)} seats")
        logger.debug(f"Seat info details: {self.seat_info}")
        logger.debug(f"Seat details: {self.seat_details}")

    def get_seat_info_by_screen_line(self, screen_line):
        """Find seat info by screen line with extensive logging and space handling"""
        # Normalize input screen line by stripping whitespace
        normalized_screen_line = screen_line.strip()
        
        logger.debug(f"Searching for seat with normalized screen line: '{normalized_screen_line}'")
        logger.debug(f"Available seats: {self.seat_info}")
        
        # Search through seat_info
        for seat_number, seat_data in self.seat_info.items():
            # Normalize seat's screen line
            normalized_seat_screen_line = seat_data.get('screenLine', '').strip()
            
            logger.debug(f"Checking seat {seat_number}: '{normalized_seat_screen_line}'")
            
            # Compare normalized screen lines
            if normalized_seat_screen_line.upper() == normalized_screen_line.upper():
                # Enhance seat data with additional information
                enhanced_seat_data = seat_data.copy()
                enhanced_seat_data['participantId'] = seat_data.get('participantId') or seat_data.get('seatName')
                
                logger.debug(f"Found matching seat: {enhanced_seat_data}")
                return enhanced_seat_data
        
        # If no match found, log all details and try partial matching
        logger.error(f"No exact match found for screen line: '{normalized_screen_line}'")
        
        # Attempt partial matching
        logger.debug("Attempting partial match...")
        for seat_number, seat_data in self.seat_info.items():
            normalized_seat_screen_line = seat_data.get('screenLine', '').strip()
            
            if normalized_seat_screen_line.upper() in normalized_screen_line.upper() or \
               normalized_screen_line.upper() in normalized_seat_screen_line.upper():
                # Enhance seat data with additional information
                enhanced_seat_data = seat_data.copy()
                enhanced_seat_data['participantId'] = seat_data.get('participantId') or seat_data.get('seatName')
                
                logger.debug(f"Found partial match: {enhanced_seat_data}")
                return enhanced_seat_data
        
        # If still no match, log full details
        logger.error(f"Full seat_info: {self.seat_info}")
        return None

    async def activate_microphone(self, seat_info):
        """Activate microphone for a given seat"""
        try:
            # Simulate microphone activation if not connected to WebSocket
            if not self.websocket:
                logger.warning("WebSocket not connected. Simulating mic activation.")
                print("WebSocket not connected. Simulating mic activation.")
                return

            # Determine seat ID, use seatName as fallback
            seat_id = seat_info.get('participantId') or seat_info.get('seatName')
            
            if not seat_id:
                logger.error(f"Cannot activate microphone: No seat ID found for {seat_info}")
                return

            # Prepare activation request
            mic_request = json.dumps({
                "operation": "ActivateMicrophone",
                "parameters": {"seatid": seat_id}
            })
            
            # Send activation request
            await self.websocket.send(mic_request)
            response = await self.websocket.recv()
            
            # Check for error response
            try:
                response_data = json.loads(response)
                if response_data.get("operation") == "error":
                    error_message = response_data.get("parameters", {}).get("message", "Unknown microphone activation error")
                    logger.critical(f"Microphone activation failed for seat {seat_id}: {error_message}")
                    print(f"Microphone activation failed. Error: {error_message}")
                    return
            except json.JSONDecodeError:
                pass
            
            logger.info(f"Microphone activation response for {seat_info.get('screenLine', 'Unknown')}: {response}")
            print(f"Microphone activation response for {seat_info.get('screenLine', 'Unknown')}: {response}")
        
        except Exception as e:
            logger.error(f"Error activating microphone: {e}", exc_info=True)
            print(f"Error activating microphone: {e}")

    async def deactivate_microphone(self, seat_info):
        """Deactivate microphone for a given seat"""
        try:
            # Simulate microphone deactivation if not connected to WebSocket
            if not self.websocket:
                logger.warning("WebSocket not connected. Simulating mic deactivation.")
                print("WebSocket not connected. Simulating mic deactivation.")
                return

            # Determine seat ID, use seatName as fallback
            seat_id = seat_info.get('participantId') or seat_info.get('seatName')
            
            if not seat_id:
                logger.error(f"Cannot deactivate microphone: No seat ID found for {seat_info}")
                return

            # Prepare deactivation request
            mic_request = json.dumps({
                "operation": "DeactivateMicrophone",
                "parameters": {"seatid": seat_id}
            })
            
            # Send deactivation request
            await self.websocket.send(mic_request)
            response = await self.websocket.recv()
            
            # Check for error response
            try:
                response_data = json.loads(response)
                if response_data.get("operation") == "error":
                    error_message = response_data.get("parameters", {}).get("message", "Unknown microphone deactivation error")
                    logger.critical(f"Microphone deactivation failed for seat {seat_id}: {error_message}")
                    print(f"Microphone deactivation failed. Error: {error_message}")
                    return
            except json.JSONDecodeError:
                pass
            
            logger.info(f"Microphone deactivation response for {seat_info.get('screenLine', 'Unknown')}: {response}")
            print(f"Microphone deactivation response for {seat_info.get('screenLine', 'Unknown')}: {response}")
        
        except Exception as e:
            logger.error(f"Error deactivating microphone: {e}", exc_info=True)
            print(f"Error deactivating microphone: {e}")

    async def main_loop(self):
        """Main application loop"""
        try:
            # Start OSC server
            self.run_osc_server()
            
            # Establish WebSocket connection
            await self.connect()
            
            # Send seat names once after connection
            logger.debug(f"Total seats found: {len(self.seat_info)}")
            for seat_number, seat_data in self.seat_info.items():
                osc_address = f"/custom-variable/seat{seat_number}/value"
                value = seat_data['screenLine']  # Use screenLine (e.g., "MALTA")
                logger.debug(f"Seat {seat_number} data: {seat_data}")
                logger.info(f"Sending OSC Message - Address: {osc_address}, Value: {value}")
                self.osc_client.send_message(osc_address, value)
            
            while True:
                # Get discussion list for microphone status
                discussion_data = await self.get_discussion_list()
                
                # Store the last discussion data for mic state checking
                self.last_discussion_data = discussion_data
                
                # Process active speakers and their microphone states
                logger.debug(f"Discussion list data: {discussion_data}")
                
                active_mic_screen_lines = []
                for discussion in discussion_data.get('parameters', {}).get('discussionList', []):
                    mic_state = discussion.get('microphoneState', 'off')
                    screen_line = discussion.get('screenLine', 'Unknown')
                    logger.debug(f"Discussion item: mic_state={mic_state}, screen_line={screen_line}")
                    
                    # If microphone is on, add to active mic screen lines
                    if mic_state == 'on':
                        active_mic_screen_lines.append(screen_line)
                
                logger.debug(f"Active mic screen lines: {active_mic_screen_lines}")
                
                # Send microphone state
                if active_mic_screen_lines:
                    osc_address = "/custom-variable/mic_on/value"
                    value = active_mic_screen_lines[0] if active_mic_screen_lines else ''
                    logger.info(f"Sending OSC Message - Address: {osc_address}, Value: {value}")
                    self.osc_client.send_message(osc_address, value)
                
                # Reduce polling interval to 500ms
                await asyncio.sleep(0.5)
        
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
        finally:
            # Stop OSC server
            self.stop_osc_server()
            
            # Close WebSocket
            if self.websocket:
                await self.websocket.close()

async def main():
    # Check and potentially configure the application
    if not check_configuration():
        print("Configuration required. Please complete the setup.")
        return

    # Rest of the existing main function remains the same
    bridge = DicentisOscBridge()
    await bridge.main_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}", exc_info=True)
