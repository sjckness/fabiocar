import ast
import re
import subprocess
import requests

import rclpy
from rclpy.node import Node


class LLMMpcTuner(Node):

    def __init__(self):
        super().__init__('llm_mpc_tuner')

        self.mpc_url = 'http://127.0.0.1:8082/completion'
        self.target_node = '/frenet_mpc_controller'

        self.done = False

        self.timer = self.create_timer(
            1.0,
            self.loop
        )

        self.get_logger().info(
            'LLMxMPC tuner ready; will trigger once after 5 seconds'
        )

    def clamp(self, name, value):

        ranges = {
            'qv': (0.0, 20.0),
            'qn': (0.0, 100.0),
            'qalpha': (0.0, 100.0),
            'qac': (0.0, 1.0),
            'qddelta': (0.0, 100.0),
            'alat_max': (0.0, 20.0),
            'a_min': (-5.0, 0.0),
            'a_max': (0.0, 5.0),
            'v_min': (0.0, 0.3),
            'v_max': (0.2, 1.0),
            'v_ref': (0.2, 1.0),
        }

        lo, hi = ranges[name]

        return max(
            lo,
            min(float(value), hi)
        )

    def parse_params(self, text):

        match = re.search(
            r'new_mpc_params\s*=\s*(\{.*?\})',
            text,
            re.DOTALL
        )

        if not match:
            raise RuntimeError(
                'No new_mpc_params found in LLM output'
            )

        params = ast.literal_eval(
            match.group(1)
        )

        allowed = {
            'qv',
            'qn',
            'qalpha',
            'qac',
            'qddelta',
            'alat_max',
            'a_min',
            'a_max',
            'v_min',
            'v_max',
            'v_ref'
        }

        clean = {}

        for k, v in params.items():

            if k in allowed:
                clean[k] = self.clamp(k, v)

        return clean

    def ask_llm(self):

        prompt = """
You are an AI assistant helping to tune the parameters of an MPC controller for an autonomous racing car.

Context:
The car is running a kinematic MPC in Frenet-like coordinates.
The trajectory is:
- straight
- smooth right 90-degree curve
- straight again

Current behavior:
- initial target speed is 0.45 m/s
- objective is smooth driving at 0.5 m/s
- avoid aggressive steering oscillations
- avoid aggressive acceleration

Tuneable MPC parameters:
qv 0, 20, 10
qn 0, 100, 20
qalpha 0, 100, 7
qac 0, 1, 0.01
qddelta 0, 100, 0.1
alat_max 0, 20, 10
a_min -5, 0, -2
a_max 0, 5, 2
v_min 0, 0.3, 0
v_max 0.2, 1.0, 0.6
v_ref 0.2, 1.0, 0.5

Task:
Adapt the tunable parameters of the MPC so that the car achieves:
Track v ref at  0.9 m/s accurately, 
Drive smooth.

Constraints:
- do not invent new parameters
- output ONLY valid Python assignment
- avoid unsafe/aggressive parameters

Output format:

new_mpc_params = {
'qv': value,
...
}
"""

        response = requests.post(
            self.mpc_url,
            json={
                'prompt': prompt,
                'n_predict': 256,
                'temperature': 0.1,
            },
            timeout=120,
        )

        response.raise_for_status()

        data = response.json()

        return data.get(
            'content',
            ''
        )

    def set_params(self, params):

        for name, value in params.items():

            cmd = [
                'ros2',
                'param',
                'set',
                self.target_node,
                name,
                str(float(value))
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )

            if result.returncode != 0:

                raise RuntimeError(
                    f'Failed setting {name}: {result.stderr}'
                )

            self.get_logger().info(
                f'Set {name} = {value}'
            )

    def loop(self):

        if self.done:
            return

        self.done = True

        try:

            self.get_logger().info(
                'Calling MPCxLLM...'
            )

            raw = self.ask_llm()

            self.get_logger().info(
                f'LLM raw output: {raw}'
            )

            params = self.parse_params(raw)

            self.get_logger().info(
                f'Parsed params: {params}'
            )

            self.set_params(params)

            self.get_logger().info(
                'LLMxMPC update complete'
            )

        except Exception as exc:

            self.get_logger().error(
                f'LLMxMPC failed: {exc}'
            )


def main(args=None):

    rclpy.init(args=args)

    node = LLMMpcTuner()

    rclpy.spin(node)

    node.destroy_node()

    rclpy.shutdown()


if __name__ == '__main__':
    main()
