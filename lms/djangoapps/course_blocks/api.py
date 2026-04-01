"""
API entry point to the course_blocks app with top-level
get_course_blocks function.
"""


from common.djangoapps.util import milestones_helpers
from django.conf import settings
from edx_when import field_data
from opaque_keys.edx.keys import UsageKey

from lms.djangoapps.course_api.blocks.transformers.block_completion import BlockCompletionTransformer
from openedx.core.djangoapps.content.block_structure.api import get_block_structure_manager
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from openedx.features.content_type_gating.block_transformers import ContentTypeGateTransformer

from .transformers import library_content, load_override_data, start_date, user_partitions, visibility
from .usage_info import CourseUsageInfo

INDIVIDUAL_STUDENT_OVERRIDE_PROVIDER = (
    'lms.djangoapps.courseware.student_field_overrides.IndividualStudentOverrideProvider'
)


def has_individual_student_override_provider():
    """
    check if FIELD_OVERRIDE_PROVIDERS has class
    `lms.djangoapps.courseware.student_field_overrides.IndividualStudentOverrideProvider`
    """
    return INDIVIDUAL_STUDENT_OVERRIDE_PROVIDER in getattr(settings, 'FIELD_OVERRIDE_PROVIDERS', ())


def get_course_block_access_transformers(user):
    """
    Default list of transformers for manipulating course block structures
    based on the user's access to the course blocks.

    Arguments:
        user (django.contrib.auth.models.User) - User object for
            which the block structure is to be transformed.

    """
    course_block_access_transformers = [
        library_content.ContentLibraryTransformer(),
        library_content.ContentLibraryOrderTransformer(),
        start_date.StartDateTransformer(),
        ContentTypeGateTransformer(),
        user_partitions.UserPartitionTransformer(),
        visibility.VisibilityTransformer(),
        field_data.DateOverrideTransformer(user),
    ]

    if has_individual_student_override_provider():
        course_block_access_transformers += [load_override_data.OverrideDataTransformer(user)]

    return course_block_access_transformers


def get_course_blocks(
        user,
        starting_block_usage_key,
        transformers=None,
        collected_block_structure=None,
        allow_start_dates_in_future=False,
        include_completion=False,
        include_has_scheduled_content=False,
):
    """
    A higher order function implemented on top of the
    block_structure.get_blocks function returning a transformed block
    structure for the given user starting at starting_block_usage_key.

    Arguments:
        user (django.contrib.auth.models.User) - User object for
            which the block structure is to be transformed.

        starting_block_usage_key (UsageKey) - Specifies the starting block
            of the block structure that is to be transformed.

        transformers (BlockStructureTransformers) - A collection of
            transformers whose transform methods are to be called.
            If None, get_course_block_access_transformers() is used.

        collected_block_structure (BlockStructureBlockData) - A
            block structure retrieved from a prior call to
            BlockStructureManager.get_collected.  Can be optionally
            provided if already available, for optimization.

    Returns:
        BlockStructureBlockData - A transformed block structure,
            starting at starting_block_usage_key, that has undergone the
            transform methods for the given user and the course
            associated with the block structure.  If using the default
            transformers, the transformed block structure will be
            exactly equivalent to the blocks that the given user has
            access.
    """
    if not transformers:
        transformers = BlockStructureTransformers(get_course_block_access_transformers(user))
    if include_completion:
        transformers += [BlockCompletionTransformer()]
    transformers.usage_info = CourseUsageInfo(
        starting_block_usage_key.course_key,
        user,
        allow_start_dates_in_future,
        include_has_scheduled_content
    )

    block_structure = get_block_structure_manager(starting_block_usage_key.course_key).get_transformed(
        transformers,
        starting_block_usage_key,
        collected_block_structure,
        user,
    )

    # Add unit gating information to vertical blocks
    # TODO: Uncomment after server restart
    _add_unit_gating_info(block_structure, user)

    return block_structure


def _add_unit_gating_info(block_structure, user):
    """
    Add unit gating information to vertical blocks in the block structure.

    This function checks if unit gating is enabled for the course and
    adds gating information to vertical blocks that have prerequisites.
    """
    try:
        # Get the course to check if unit gating is enabled
        course = block_structure.get_course()
        if not getattr(course, 'enable_unit_gating', False):
            return

        # Process each block in the structure
        for block_key in block_structure:
            block = block_structure.get_xblock(block_key)
            if block and block.location.block_type == 'vertical':
                # Get unit gating information for this vertical block
                gated_content = _get_unit_gated_content_info(block, user, course)

                if gated_content:
                    block_structure.override_xblock_field(block_key, 'gatedContent', gated_content)

    except Exception:  # pylint: disable=W0718
        # If anything fails, don't break the block structure
        pass


def _get_unit_gated_content_info(block, user, course):
    """
    Get unit gating information for a vertical block.

    This mirrors the logic from vertical_block.py._get_unit_gated_content_info
    """
    if not getattr(course, 'enable_unit_gating', False):
        return None

    # pylint: disable=R1702
    try:
        # Get gating milestone for this block
        gating_namespace = f"{block.location}.gating"

        # Check if user has fulfilled the required milestone
        user_milestones = milestones_helpers.get_user_milestones(user.id, course.id)

        # Get the prerequisite milestone for this block
        prereq_milestone = milestones_helpers.get_course_content_milestones(
            course.id,
            content_id=block.location,
            relationship='requires'
        )

        if not prereq_milestone:
            # No prerequisites set for this unit
            return None

        # Check if user has fulfilled the prerequisite
        prereq_milestone_id = prereq_milestone[0]['milestone_id']
        user_fulfilled = any(
            milestone['milestone_id'] == prereq_milestone_id and milestone['fulfilled']
            for milestone in user_milestones
        )

        gated_content = {
            'prereq_id': None,
            'prereq_url': None,
            'prereq_section_name': None,
            'gated_section_name': block.display_name,
        }

        if not user_fulfilled:
            gated_content['gated'] = True
            # Get prerequisite info for navigation
            fulfills_milestones = milestones_helpers.get_course_content_milestones(
                course.id,
                relationship='fulfills'
            )
            for milestone in fulfills_milestones:
                if milestone['milestone_id'] == prereq_milestone_id:
                    # Find the prerequisite block
                    prereq_content_key = milestone.get('content_key')
                    if prereq_content_key:
                        prereq_usage_key = UsageKey.from_string(prereq_content_key)
                        try:
                            # Check if modulestore is available (not available in test environment)
                            if hasattr(block.runtime, 'modulestore'):
                                prereq_block = block.runtime.modulestore.get_item(prereq_usage_key)
                                if prereq_block:
                                    gated_content.update({
                                        'prereq_id': str(prereq_block.location),
                                        'prereq_url': f"/course/{course.id}/jump_to/{prereq_block.location}",
                                        'prereq_section_name': prereq_block.display_name,
                                    })
                        except Exception:  # pylint: disable=W0718
                            pass
                    break
        else:
            gated_content['gated'] = False

        return gated_content

    except Exception:  # pylint: disable=W0718
        # If anything fails, don't gate the content
        return None
