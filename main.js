const { InstanceBase, runEntrypoint, InstanceStatus } = require('@companion-module/base')
const WebSocket = require('ws')
const objectPath = require('object-path')

// Placeholder for upgrade scripts
const upgradeScripts = []

class BoschDicentisInstance extends InstanceBase {
	constructor(internal) {
		super(internal)
		this.isInitialized = false
		this.ws = null
		this.reconnect_timer = null
		this.poll_timer = null
		this.subscriptions = new Map()
		this.seats = {}
		this.wsRegex = '^wss?:\\/\\/([\\da-z\\.-]+)(:\\d{1,5})?(?:\\/(.*))?$'
		this.activeMics = {}
		this.participants = []
		this.discussionList = []
	}

	async init(config) {
		this.config = config
		this.initWebSocket()
		this.isInitialized = true

		// Add a default subscription for mic state
		this.subscriptions.set('mic_on', {
			variableName: 'mic_on',
			subpath: ''
		})

		this.updateVariables()
		this.initActions()
		this.initFeedbacks()
		this.initPresets()
		this.subscribeFeedbacks()
	}

	updateVariables(callerId = null) {
		let variables = new Set()
		let defaultValues = {}

		// Add variables from seats
		if (this.seats) {
			Object.keys(this.seats).forEach(screenLine => {
				const seat = this.seats[screenLine]
				variables.add(seat.name)
				if (callerId === null) {
					defaultValues[seat.name] = ''
				}
			})
		}

		// Add mic_on variable
		variables.add('mic_on')
		if (callerId === null) {
			defaultValues['mic_on'] = ''
		}

		let variableDefinitions = []
		variables.forEach((variable) => {
			variableDefinitions.push({
				name: variable,
				variableId: variable,
			})
		})

		this.setVariableDefinitions(variableDefinitions)
		if (this.config.reset_variables) {
			this.setVariableValues(defaultValues)
		}
	}

	async destroy() {
		this.isInitialized = false
		if (this.reconnect_timer) {
			clearTimeout(this.reconnect_timer)
			this.reconnect_timer = null
		}
		if (this.poll_timer) {
			clearInterval(this.poll_timer)
			this.poll_timer = null
		}
		if (this.ws) {
			this.ws.close(1000)
			delete this.ws
		}
	}

	async configUpdated(config) {
		this.config = config
		this.initWebSocket()
	}

	initWebSocket() {
		if (this.reconnect_timer) {
			clearTimeout(this.reconnect_timer)
			this.reconnect_timer = null
		}

		const url = `wss://${this.config.server_ip}:31416/Dicentis/API`
		
		if (!url || url.match(new RegExp(this.wsRegex)) === null) {
			this.updateStatus(InstanceStatus.BadConfig, `WS URL is not defined or invalid`)
			return
		}

		this.updateStatus(InstanceStatus.Connecting)

		if (this.ws) {
			this.ws.close(1000)
			delete this.ws
		}

		// Configure WebSocket to ignore SSL certificate validation
		this.ws = new WebSocket(url, 'DICENTIS_1_0', {
			rejectUnauthorized: false,  // Ignore SSL certificate validation
			requestCert: false,
			agent: false
		})

		this.ws.on('open', () => {
			this.updateStatus(InstanceStatus.Ok)
			this.log('debug', `Connection opened`)
			
			// Attempt login
			this.login()

			// Set up polling only after connection is established
			const pollInterval = Math.max(100, Math.min(10000, parseInt(this.config.poll_interval || 1000)))
			if (this.poll_timer) {
				clearInterval(this.poll_timer)
			}
			this.poll_timer = setInterval(() => {
				this.retrieveActiveMics()
			}, pollInterval)

			if (this.config.reset_variables) {
				this.updateVariables()
			}
		})

		this.ws.on('close', (code) => {
			this.log('debug', `Connection closed with code ${code}`)
			this.updateStatus(InstanceStatus.Disconnected, `Connection closed with code ${code}`)
			
			// Clear polling timer
			if (this.poll_timer) {
				clearInterval(this.poll_timer)
				this.poll_timer = null
			}
			
			this.maybeReconnect()
		})

		this.ws.on('message', this.messageReceivedFromWebSocket.bind(this))

		this.ws.on('error', (data) => {
			this.log('error', `WebSocket error: ${data}`)
		})
	}

	maybeReconnect() {
		if (this.isInitialized && this.config.reconnect) {
			if (this.reconnect_timer) {
				clearTimeout(this.reconnect_timer)
			}
			this.reconnect_timer = setTimeout(() => {
				this.initWebSocket()
			}, 5000)
		}
	}

	retrieveActiveMics() {
		// Only send if WebSocket is open
		if (this.ws && this.ws.readyState === WebSocket.OPEN) {
			const activeMicsPayload = {
				operation: 'GetActiveMicrophones',
				parameters: {}
			}

			this.ws.send(JSON.stringify(activeMicsPayload))
		}
	}

	login() {
		// Send login payload with credentials in the specified format
		const loginPayload = {
			operation: 'login',
			parameters: {
				user: `${this.config.username}`,
				password: `${this.config.password}`
			}
		}
		this.ws.send(JSON.stringify(loginPayload))

		// Request initial data after login
		this.requestParticipants()
		this.requestDiscussionList()
		
		// Request and log permissions
		this.requestPermissions()
	}

	requestParticipants() {
		const payload = {
			operation: 'getseats',
			parameters: {}
		}
		this.ws.send(JSON.stringify(payload))
	}

	requestDiscussionList() {
		const payload = {
			operation: 'GetDiscussionList',
			parameters: {
				"method": "GetDiscussionList",
				"parameters": {}
			}
		}
		this.ws.send(JSON.stringify(payload))
	}

	requestPermissions() {
		const payload = {
			operation: 'GetPermissions',
			parameters: {}
		}
		this.ws.send(JSON.stringify(payload))
	}

	messageReceivedFromWebSocket(data) {
		if (this.config.debug_messages) {
			this.log('debug', `Message received: ${data}`)
		}

		let msgValue = null
		try {
			msgValue = JSON.parse(data)
		} catch (e) {
			this.log('error', `Failed to parse message: ${data}`)
			return
		}

		// Handle different message types
		if (msgValue.participants) {
			this.processParticipants(msgValue.participants)
		}

		if (msgValue.discussionList) {
			this.processDiscussionList(msgValue.discussionList)
		}

		// Log permissions if received
		if (msgValue.operation === 'GetPermissions') {
			this.log('info', `User Permissions: ${JSON.stringify(msgValue.parameters, null, 2)}`)
		}
	}

	processParticipants(participants) {
		this.participants = participants
		
		// Update seats based on participants
		const newSeats = {}
		participants.forEach(participant => {
			const screenLine = participant.screenLine || participant.participantId
			newSeats[screenLine] = {
				id: participant.participantId,
				name: `${participant.firstName} ${participant.lastName}`.trim(),
				screenLine: screenLine
			}
		})

		this.seats = newSeats
		this.updateVariables()
		this.initActions()
		this.initFeedbacks()
		this.initPresets()
	}

	processDiscussionList(discussionList) {
		// Store the full discussion list
		this.discussionList = discussionList

		// Track microphones that are 'on'
		this.mic_on = discussionList
			.filter(item => item.microphoneState === 'on')
			.map(item => item.screenLine)

		// Log the microphones that are on (if debug is enabled)
		if (this.config.debug_messages) {
			this.log('debug', `Microphones On: ${JSON.stringify(this.mic_on)}`)
		}

		// Update active microphones based on discussion list
		const activeMicIds = discussionList
			.filter(item => item.microphoneState === 'on')
			.map(item => item.seatId)

		// Update active mics for each seat
		Object.keys(this.seats).forEach(screenLine => {
			const seat = this.seats[screenLine]
			this.activeMics[screenLine] = activeMicIds.includes(seat.id)
		})

		this.updateVariables()
		this.initPresets()
	}

	initActions() {
		const actions = {}

		Object.keys(this.seats).forEach(screenLine => {
			const seat = this.seats[screenLine]
			actions[`toggle_mic_${seat.name}`] = {
				name: `Toggle Microphone for ${seat.name}`,
				options: [],
				callback: async () => {
					const currentState = this.activeMics[screenLine] || false
					const payload = {
						operation: currentState ? 'DeactivateMicrophone' : 'ActivateMicrophone',
						parameters: { seatid: seat.id }
					}
					
					if (this.ws && this.ws.readyState === WebSocket.OPEN) {
						this.ws.send(JSON.stringify(payload))
						
						// Optimistically update local state
						this.activeMics[screenLine] = !currentState
						this.initPresets()
					}
				}
			}
		})

		this.setActionDefinitions(actions)
	}

	initFeedbacks() {
		const feedbacks = {
			mic_active: {
				type: 'boolean',
				name: 'Microphone Active',
				description: 'Change button color when microphone is active',
				defaultStyle: {
					color: 'white',
					bgcolor: 'black'
				},
				options: [
					{
						type: 'dropdown',
						label: 'Seat',
						id: 'seat',
						choices: Object.keys(this.seats).map(screenLine => ({
							id: this.seats[screenLine].name,
							label: this.seats[screenLine].name
						}))
					}
				],
				callback: (feedback) => {
					const seatName = feedback.options.seat
					return this.activeMics[Object.keys(this.seats).find(screenLine => 
						this.seats[screenLine].name === seatName)] || false
				},
				style: (feedback) => {
					return {
						color: 'white',
						bgcolor: this.activeMics[Object.keys(this.seats).find(screenLine => 
							this.seats[screenLine].name === feedback.options.seat)] ? 'red' : 'black'
					}
				}
			}
		}
		this.setFeedbackDefinitions(feedbacks)
	}

	initPresets() {
		const presets = []

		// Individual seat microphone presets
		Object.keys(this.seats).forEach(screenLine => {
			const seat = this.seats[screenLine]
			presets.push({
				category: 'Seat Microphones',
				name: `${seat.name} Microphone Control`,
				type: 'button',
				style: {
					text: seat.name,
					size: 'auto',
					color: 'white',
					bgcolor: 'black'
				},
				steps: [
					{
						down: [
							{
								actionId: `toggle_mic_${seat.name}`,
								options: {}
							}
						]
					}
				],
				feedbacks: [
					{
						feedbackId: 'mic_active',
						options: {
							seat: seat.name
						},
						style: {
							color: 'white',
							bgcolor: 'red'
						}
					}
				]
			})
		})

		// Global microphone status preset
		const activeSeat = Object.keys(this.seats).find(screenLine => 
			this.activeMics[screenLine] === true)

		presets.push({
			category: 'Global Microphones',
			name: 'Active Microphone Status',
			type: 'button',
			style: {
				text: activeSeat ? `Active: ${this.seats[activeSeat].name}` : 'No Active Mic',
				size: 'auto',
				color: 'white',
				bgcolor: 'black'
			},
			steps: [
				{
					down: [
						{
							actionId: activeSeat ? `toggle_mic_${this.seats[activeSeat].name}` : '',
							options: {}
						}
					]
				}
			],
			feedbacks: [
				{
					feedbackId: 'mic_active',
					options: {
						seat: activeSeat ? this.seats[activeSeat].name : ''
					},
					style: {
						color: 'white',
						bgcolor: Object.values(this.activeMics).some(active => active) ? 'purple' : 'black'
					}
				}
			]
		})

		this.setPresetDefinitions(presets)
	}

	subscribeFeedbacks() {
		// Placeholder for feedback subscriptions
	}

	activateMic(screenLine) {
		if (!this.ws) return

		const seat = this.seats[screenLine]
		if (!seat) {
			this.log('error', `No seat found for screen line: ${screenLine}`)
			return
		}

		const activatePayload = {
			operation: 'ActivateMicrophone',
			parameters: { seatid: seat.id }
		}

		this.ws.send(JSON.stringify(activatePayload))
	}

	deactivateMic(screenLine) {
		if (!this.ws) return

		const seat = this.seats[screenLine]
		if (!seat) {
			this.log('error', `No seat found for screen line: ${screenLine}`)
			return
		}

		const deactivatePayload = {
			operation: 'DeactivateMicrophone',
			parameters: { seatid: seat.id }
		}

		this.ws.send(JSON.stringify(deactivatePayload))
	}

	getConfigFields() {
		return [
			{
				type: 'textinput',
				id: 'server_ip',
				label: 'Server IP',
				width: 8,
				regex: '/^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/'
			},
			{
				type: 'textinput',
				id: 'username',
				label: 'Username',
				width: 8
			},
			{
				type: 'textinput',
				id: 'password',
				label: 'Password',
				width: 8,
				default: '',
				required: false
			},
			{
				type: 'number',
				id: 'poll_interval',
				label: 'Active Mic Poll Interval (ms)',
				width: 6,
				default: 1000,
				min: 100,
				max: 10000,
				tooltip: 'How often to check for active microphones (100-10000 ms)'
			},
			{
				type: 'checkbox',
				id: 'reconnect',
				label: 'Reconnect',
				tooltip: 'Reconnect on WebSocket error (after 5 secs)',
				width: 6,
				default: true
			},
			{
				type: 'checkbox',
				id: 'reset_variables',
				label: 'Reset Variables',
				tooltip: 'Reset variables on init and connect',
				width: 6,
				default: true
			},
			{
				type: 'checkbox',
				id: 'debug_messages',
				label: 'Debug Messages',
				tooltip: 'Log WebSocket messages',
				width: 6,
				default: false
			}
		]
	}
}

runEntrypoint(BoschDicentisInstance, upgradeScripts)
