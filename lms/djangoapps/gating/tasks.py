"""
This file contains celery tasks related to course content gating.
"""


import logging

from celery import shared_task
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey, UsageKey

from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.gating import api as gating_api
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


@shared_task
@set_code_owner_attribute
def task_evaluate_subsection_completion_milestones(course_id, block_id, user_id):
    """
    Evaluates gating milestone relationships attached to the given subsection.

    Arguments:
        course_id (str): The course key
        block_id (str): The subsection block usage key
        user_id (int): The id of the user

    Returns:
        None
    """
    try:
        user = User.objects.get(id=user_id)
        usage_key = UsageKey.from_string(block_id).map_into_course(course_id)
        gating_api.evaluate_prerequisite(usage_key, user)
    except Exception as exc:
        log.error("Failed to evaluate gating milestones for course %s, block %s, user %s: %s", course_id, block_id, user_id, exc)


@shared_task
def task_evaluate_unit_completion_milestones(course_id, block_id, user_id):
    """
    Evaluates unit-level gating milestone relationships attached to the given unit.

    Arguments:
        course_id (str): The course key
        block_id (str): The unit block usage key
        user_id (int): The id of the user

    Returns:
        None
    """
    try:
        user = User.objects.get(id=user_id)
        usage_key = UsageKey.from_string(block_id).map_into_course(course_id)
        
        # Get the course to check if unit gating is enabled
        from xmodule.modulestore.django import modulestore
        store = modulestore()
        course = store.get_course(usage_key.course_key)
        
        if getattr(course, 'enable_unit_gating', False):
            # Create a mock unit grade object for evaluation
            from lms.djangoapps.grades.api import SubsectionGradeFactory
            from lms.djangoapps.course_blocks.api import get_course_blocks
            
            unit_structure = get_course_blocks(user, usage_key)
            if any(unit_structure) and usage_key in unit_structure:
                unit_grade_factory = SubsectionGradeFactory(user, course_structure=unit_structure)
                unit_grade = unit_grade_factory.update(unit_structure[usage_key])
                gating_api.evaluate_unit_prerequisite(course, unit_grade, user)
    except Exception as exc:
        log.error("Failed to evaluate unit gating milestones for course %s, block %s, user %s: %s", course_id, block_id, user_id, exc)


def _get_subsection_of_block(usage_key, block_structure):
    """
    Finds subsection of a block by recursively iterating over its parents
    :param usage_key: key of the block
    :param block_structure: block structure
    :return: sequential block
    """
    parents = block_structure.get_parents(usage_key)
    if parents:
        for parent_block in parents:
            if parent_block.block_type == 'sequential':
                return parent_block
            else:
                return _get_subsection_of_block(parent_block, block_structure)
