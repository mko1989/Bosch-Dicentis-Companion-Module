# Bosch Dicentis Companion Module

#### Config
- **Server IP**: IP address of the Bosch Dicentis server
- **Username**: Login credentials for API access
- **Password**: Authentication password
- **Reconnect**: Automatically attempt to reconnect if the connection is lost (recommended)
- **Reset Variables**: Reset variables on initialization and reconnection

##### User Credentials
The username and password is configured in this file:
`(C:\ProgramData\Bosch\DICENTIS\API\Conference_Protocol_user.jsonc)` on the Server PC

#### Debug Messages
- Enable debug messages to track WebSocket connections, API interactions, and system events
- Debug logs provide detailed information about:
  - WebSocket connection status
  - Authentication attempts
  - API request and response details
  - Microphone state changes
  - Permission validation
- Useful for troubleshooting connection issues, permission problems, and unexpected system behavior

#### Variables
- `$(dicentis:{seatName})`: Displays the screen line for each seat
- `$(dicentis:mic_on)`: Indicates the current microphone state
- Seat-specific variables named after participant

#### Actions
- Toggle microphone for each seat
- Activate/Deactivate individual microphones

#### Permissions

`canManageMeeting`
 In order to have canManageMeeting permission, web client seat needs to have Manage Meeting rights and logged in user needs to have activate meeting rights or logged in Participant needs to have manage meeting.
 
`canControlInterpretation`
 In order to have canControlInterpretation permission, web client seat needs to have Manage Meeting rights, system should have special licenses, premium licenses or ultimate licenses and logged in user needs to have ActivateMeeting or logged in participant needs to have manage meeting.
 
`canEditSynoptic`
 In order to have canEditSynoptic permission, logged in user needs to have ConfigureSystem rights.
 
`canSelect`
 In order to have canSelect permission, logged in user needs to have ConfigureSystem rights.
 
`canAddDevicesToSeats`
 In order to have canAddDevicesToSeats permission, logged in user needs to have ConfigureSystem rights.
 
`canViewVoting`
 In order to have canViewVoting permission, system should have special licenses, premium licenses or ultimate licenses, the synoptic seat needs to have Can Vote, and Voting Scripts should be configured to have rights for External System.
 
`canEnableSeatIllumination`
 In order to have canEnableSeatIllumination permission, logged in user needs to have ConfigureSystem rights.
 
`canSwitchSystemPowerOn`
 In order to have canSwitchSystemPowerOn, web client seat needs to have Power On rights. Logged in participant needs to have manage meeting rights.
 
`canSwitchSystemPowerOff`
 In order to have canSwitchSystemPowerOff, web client seat needs to have Power Off rights. Logged in participant needs to have manage meeting rights.
 
`canViewRequestItems`
 In order to have canViewRequestItems, logged-in user either needs to have Manage Meeting rights or logged-in user needs to have Special permission.
 
`canViewFirstRequestOnSeat`
 In order to have canViewRequestItems, logged in user either needs to have Manage Meeting rights or logged-in user needs to have Special permission.
 
`canDeactivateMicrophone`
 This permission only gets enabled in Response mode, logged in user needs to have Manage rights.
 
`hasSpecialPermission`
 In order to have hasSpecialPermission. Web client seat needs to have special permission.
 
`canViewInterpretation`
 In order to have canViewInterpretation, Logged in user either needs to have Activate meeting rights. Logged in participant needs to have Manage Meeting rights.
 
`canControlMasterVolume`
 In order to have canControlMasterVolume, web client seat needs to have Volume Control rights. Logged in user either needs to have Activate meeting rights. Logged in participant needs to have Manage Meeting rights.
 
`canControlVoting`
 In order to have canControlVoting, system should have LVPM license, web client seat needs to have Voting Control rights. Logged in user needs to have Activate meeting rights. Logged in participant needs to have Manage Meeting rights and Vote rights.
