def lambda_handler(event, context):
    print("context:", context)
    print("event:", event)
    ip = event["requestContext"]["http"]["sourceIp"]
    print("Received request from IP:", ip)

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Debug test successful", "ip": ip})
    }
